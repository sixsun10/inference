# Copyright 2022-2023 XProbe Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import logging
import os
import sys
import warnings
from typing import List, Optional, Tuple, Union

import click
from xoscar.utils import get_next_port

from .. import __version__
from ..client import RESTfulClient
from ..client.restful.restful_client import (
    RESTfulChatglmCppChatModelHandle,
    RESTfulChatModelHandle,
    RESTfulGenerateModelHandle,
)
from ..constants import (
    XINFERENCE_AUTH_DIR,
    XINFERENCE_DEFAULT_DISTRIBUTED_HOST,
    XINFERENCE_DEFAULT_ENDPOINT_PORT,
    XINFERENCE_DEFAULT_LOCAL_HOST,
    XINFERENCE_ENV_ENDPOINT,
    XINFERENCE_LOG_BACKUP_COUNT,
    XINFERENCE_LOG_MAX_BYTES,
)
from ..isolation import Isolation
from ..types import ChatCompletionMessage
from .utils import (
    get_config_dict,
    get_log_file,
    get_timestamp_ms,
    handle_click_args_type,
)

try:
    # provide elaborate line editing and history features.
    # https://docs.python.org/3/library/functions.html#input
    import readline  # noqa: F401
except ImportError:
    pass


def get_endpoint(endpoint: Optional[str]) -> str:
    # user didn't specify the endpoint.
    if endpoint is None:
        if XINFERENCE_ENV_ENDPOINT in os.environ:
            return os.environ[XINFERENCE_ENV_ENDPOINT]
        else:
            default_endpoint = f"http://{XINFERENCE_DEFAULT_LOCAL_HOST}:{XINFERENCE_DEFAULT_ENDPOINT_PORT}"
            return default_endpoint
    else:
        return endpoint


def get_hash_endpoint(endpoint: str) -> str:
    import hashlib

    m = hashlib.sha256()
    m.update(bytes(endpoint, "utf-8"))
    return m.hexdigest()


def get_stored_token(
    endpoint: str, client: Optional[RESTfulClient] = None
) -> Optional[str]:
    rest_client = RESTfulClient(endpoint) if client is None else client
    authed = rest_client._cluster_authed
    if not authed:
        return None

    token_path = os.path.join(XINFERENCE_AUTH_DIR, get_hash_endpoint(endpoint))
    if not os.path.exists(token_path):
        raise RuntimeError("Cannot find access token, please login first!")
    with open(token_path, "r") as f:
        access_token = str(f.read())
    return access_token


def start_local_cluster(
    log_level: str,
    host: str,
    port: int,
    metrics_exporter_host: Optional[str] = None,
    metrics_exporter_port: Optional[int] = None,
    auth_config_file: Optional[str] = None,
):
    from .local import main

    dict_config = get_config_dict(
        log_level,
        get_log_file(f"local_{get_timestamp_ms()}"),
        XINFERENCE_LOG_BACKUP_COUNT,
        XINFERENCE_LOG_MAX_BYTES,
    )
    logging.config.dictConfig(dict_config)  # type: ignore

    main(
        host=host,
        port=port,
        metrics_exporter_host=metrics_exporter_host,
        metrics_exporter_port=metrics_exporter_port,
        logging_conf=dict_config,
        auth_config_file=auth_config_file,
    )


@click.group(
    invoke_without_command=True,
    name="xinference",
    help="Xinference command-line interface for serving and deploying models.",
)
@click.pass_context
@click.version_option(
    __version__,
    "--version",
    "-v",
    help="Show the current version of the Xinference tool.",
)
@click.option(
    "--log-level",
    default="INFO",
    type=str,
    help="""Set the logger level. Options listed from most log to least log are:
              DEBUG > INFO > WARNING > ERROR > CRITICAL (Default level is INFO)""",
)
@click.option(
    "--host",
    "-H",
    default=XINFERENCE_DEFAULT_LOCAL_HOST,
    type=str,
    help="Specify the host address for the Xinference server.",
)
@click.option(
    "--port",
    "-p",
    default=XINFERENCE_DEFAULT_ENDPOINT_PORT,
    type=int,
    help="Specify the port number for the Xinference server.",
)
def cli(
    ctx,
    log_level: str,
    host: str,
    port: int,
):
    if ctx.invoked_subcommand is None:
        # Save the current state of the warning filter.
        with warnings.catch_warnings():
            warnings.simplefilter("always", DeprecationWarning)
            warnings.warn(
                "Starting a local 'xinference' cluster via the 'xinference' command line is "
                "deprecated and will be removed in a future release. Please use the new "
                "'xinference-local' command.",
                category=DeprecationWarning,
            )

        start_local_cluster(log_level=log_level, host=host, port=port)


@click.command(help="Starts an Xinference local cluster.")
@click.option(
    "--log-level",
    default="INFO",
    type=str,
    help="""Set the logger level. Options listed from most log to least log are:
              DEBUG > INFO > WARNING > ERROR > CRITICAL (Default level is INFO)""",
)
@click.option(
    "--host",
    "-H",
    default=XINFERENCE_DEFAULT_LOCAL_HOST,
    type=str,
    help="Specify the host address for the Xinference server.",
)
@click.option(
    "--port",
    "-p",
    default=XINFERENCE_DEFAULT_ENDPOINT_PORT,
    type=int,
    help="Specify the port number for the Xinference server.",
)
@click.option(
    "--metrics-exporter-host",
    "-MH",
    default=None,
    type=str,
    help="Specify the host address for the Xinference metrics exporter server, default is the same as --host.",
)
@click.option(
    "--metrics-exporter-port",
    "-mp",
    type=int,
    help="Specify the port number for the Xinference metrics exporter server.",
)
@click.option(
    "--auth-config",
    type=str,
    help="Specify the auth config json file.",
)
def local(
    log_level: str,
    host: str,
    port: int,
    metrics_exporter_host: Optional[str],
    metrics_exporter_port: Optional[int],
    auth_config: Optional[str],
):
    if metrics_exporter_host is None:
        metrics_exporter_host = host
    start_local_cluster(
        log_level=log_level,
        host=host,
        port=port,
        metrics_exporter_host=metrics_exporter_host,
        metrics_exporter_port=metrics_exporter_port,
        auth_config_file=auth_config,
    )


@click.command(
    help="Starts an Xinference supervisor to control and monitor the worker actors."
)
@click.option(
    "--log-level",
    default="INFO",
    type=str,
    help="""Set the logger level for the supervisor. Options listed from most log to least log are:
              DEBUG > INFO > WARNING > ERROR > CRITICAL (Default level is INFO)""",
)
@click.option(
    "--host",
    "-H",
    default=XINFERENCE_DEFAULT_DISTRIBUTED_HOST,
    type=str,
    help="Specify the host address for the supervisor.",
)
@click.option(
    "--port",
    "-p",
    default=XINFERENCE_DEFAULT_ENDPOINT_PORT,
    type=int,
    help="Specify the port number for the Xinference web ui and service.",
)
@click.option(
    "--supervisor-port",
    type=int,
    help="Specify the port number for the Xinference supervisor.",
)
@click.option(
    "--auth-config",
    type=str,
    help="Specify the auth config json file.",
)
def supervisor(
    log_level: str,
    host: str,
    port: int,
    supervisor_port: Optional[int],
    auth_config: Optional[str],
):
    from ..deploy.supervisor import main

    dict_config = get_config_dict(
        log_level,
        get_log_file(f"supervisor_{get_timestamp_ms()}"),
        XINFERENCE_LOG_BACKUP_COUNT,
        XINFERENCE_LOG_MAX_BYTES,
    )
    logging.config.dictConfig(dict_config)  # type: ignore

    main(
        host=host,
        port=port,
        supervisor_port=supervisor_port,
        logging_conf=dict_config,
        auth_config_file=auth_config,
    )


@click.command(
    help="Starts an Xinference worker to execute tasks assigned by the supervisor in a distributed setup."
)
@click.option(
    "--log-level",
    default="INFO",
    type=str,
    help="""Set the logger level for the worker. Options listed from most log to least log are:
              DEBUG > INFO > WARNING > ERROR > CRITICAL (Default level is INFO)""",
)
@click.option("--endpoint", "-e", type=str, help="Xinference endpoint.")
@click.option(
    "--host",
    "-H",
    default=XINFERENCE_DEFAULT_DISTRIBUTED_HOST,
    type=str,
    help="Specify the host address for the worker.",
)
@click.option(
    "--worker-port",
    type=int,
    help="Specify the port number for the Xinference worker.",
)
@click.option(
    "--metrics-exporter-host",
    "-MH",
    default=XINFERENCE_DEFAULT_DISTRIBUTED_HOST,
    type=str,
    help="Specify the host address for the metrics exporter server.",
)
@click.option(
    "--metrics-exporter-port",
    type=int,
    help="Specify the port number for the Xinference metrics exporter worker.",
)
def worker(
    log_level: str,
    endpoint: Optional[str],
    host: str,
    worker_port: Optional[int],
    metrics_exporter_host: Optional[str],
    metrics_exporter_port: Optional[int],
):
    from ..deploy.worker import main

    dict_config = get_config_dict(
        log_level,
        get_log_file(f"worker_{get_timestamp_ms()}"),
        XINFERENCE_LOG_BACKUP_COUNT,
        XINFERENCE_LOG_MAX_BYTES,
    )
    logging.config.dictConfig(dict_config)  # type: ignore

    endpoint = get_endpoint(endpoint)

    client = RESTfulClient(base_url=endpoint)
    supervisor_internal_addr = client._get_supervisor_internal_address()

    address = f"{host}:{worker_port or get_next_port()}"
    main(
        address=address,
        supervisor_address=supervisor_internal_addr,
        metrics_exporter_host=metrics_exporter_host,
        metrics_exporter_port=metrics_exporter_port,
        logging_conf=dict_config,
    )


@cli.command("register", help="Register a new model with Xinference for deployment.")
@click.option("--endpoint", "-e", type=str, help="Xinference endpoint.")
@click.option(
    "--model-type",
    "-t",
    default="LLM",
    type=str,
    help="Type of model to register (default is 'LLM').",
)
@click.option("--file", "-f", type=str, help="Path to the model configuration file.")
@click.option(
    "--persist",
    "-p",
    is_flag=True,
    help="Persist the model configuration to the filesystem, retains the model registration after server restarts.",
)
@click.option(
    "--api-key",
    "-ak",
    default=None,
    type=str,
    help="Api-Key for access xinference api with authorization.",
)
def register_model(
    endpoint: Optional[str],
    model_type: str,
    file: str,
    persist: bool,
    api_key: Optional[str],
):
    endpoint = get_endpoint(endpoint)
    with open(file) as fd:
        model = fd.read()

    client = RESTfulClient(base_url=endpoint, api_key=api_key)
    if api_key is None:
        client._set_token(get_stored_token(endpoint, client))
    client.register_model(
        model_type=model_type,
        model=model,
        persist=persist,
    )


@cli.command(
    "unregister",
    help="Unregister a model from Xinference, removing it from deployment.",
)
@click.option("--endpoint", "-e", type=str, help="Xinference endpoint.")
@click.option(
    "--model-type",
    "-t",
    default="LLM",
    type=str,
    help="Type of model to unregister (default is 'LLM').",
)
@click.option("--model-name", "-n", type=str, help="Name of the model to unregister.")
@click.option(
    "--api-key",
    "-ak",
    default=None,
    type=str,
    help="Api-Key for access xinference api with authorization.",
)
def unregister_model(
    endpoint: Optional[str],
    model_type: str,
    model_name: str,
    api_key: Optional[str],
):
    endpoint = get_endpoint(endpoint)

    client = RESTfulClient(base_url=endpoint, api_key=api_key)
    if api_key is None:
        client._set_token(get_stored_token(endpoint, client))
    client.unregister_model(
        model_type=model_type,
        model_name=model_name,
    )


@cli.command("registrations", help="List all registered models in Xinference.")
@click.option(
    "--endpoint",
    "-e",
    type=str,
    help="Xinference endpoint.",
)
@click.option(
    "--model-type",
    "-t",
    default="LLM",
    type=str,
    help="Filter by model type (default is 'LLM').",
)
@click.option(
    "--api-key",
    "-ak",
    default=None,
    type=str,
    help="Api-Key for access xinference api with authorization.",
)
def list_model_registrations(
    endpoint: Optional[str],
    model_type: str,
    api_key: Optional[str],
):
    from tabulate import tabulate

    endpoint = get_endpoint(endpoint)
    client = RESTfulClient(base_url=endpoint, api_key=api_key)
    if api_key is None:
        client._set_token(get_stored_token(endpoint, client))

    registrations = client.list_model_registrations(model_type=model_type)

    table = []
    if model_type == "LLM":
        for registration in registrations:
            model_name = registration["model_name"]
            model_family = client.get_model_registration(model_type, model_name)
            table.append(
                [
                    model_type,
                    model_family["model_name"],
                    model_family["model_lang"],
                    model_family["model_ability"],
                    registration["is_builtin"],
                ]
            )
        print(
            tabulate(
                table, headers=["Type", "Name", "Language", "Ability", "Is-built-in"]
            ),
            file=sys.stderr,
        )
    elif model_type == "embedding":
        for registration in registrations:
            model_name = registration["model_name"]
            model_family = client.get_model_registration(model_type, model_name)
            table.append(
                [
                    model_type,
                    model_family["model_name"],
                    model_family["language"],
                    model_family["dimensions"],
                    registration["is_builtin"],
                ]
            )
        print(
            tabulate(
                table, headers=["Type", "Name", "Language", "Dimensions", "Is-built-in"]
            ),
            file=sys.stderr,
        )
    elif model_type == "rerank":
        for registration in registrations:
            model_name = registration["model_name"]
            model_family = client.get_model_registration(model_type, model_name)
            table.append(
                [
                    model_type,
                    model_family["model_name"],
                    model_family["language"],
                    registration["is_builtin"],
                ]
            )
        print(
            tabulate(table, headers=["Type", "Name", "Language", "Is-built-in"]),
            file=sys.stderr,
        )
    elif model_type == "image":
        for registration in registrations:
            model_name = registration["model_name"]
            model_family = client.get_model_registration(model_type, model_name)
            table.append(
                [
                    model_type,
                    model_family["model_name"],
                    model_family["model_family"],
                    registration["is_builtin"],
                ]
            )
        print(
            tabulate(table, headers=["Type", "Name", "Family", "Is-built-in"]),
            file=sys.stderr,
        )
    elif model_type == "audio":
        for registration in registrations:
            model_name = registration["model_name"]
            model_family = client.get_model_registration(model_type, model_name)
            table.append(
                [
                    model_type,
                    model_family["model_name"],
                    model_family["model_family"],
                    model_family["multilingual"],
                    registration["is_builtin"],
                ]
            )
        print(
            tabulate(
                table, headers=["Type", "Name", "Family", "Multilingual", "Is-built-in"]
            ),
            file=sys.stderr,
        )
    else:
        raise NotImplementedError(f"List {model_type} is not implemented.")


@cli.command(
    "launch",
    help="Launch a model with the Xinference framework with the given parameters.",
    context_settings=dict(
        ignore_unknown_options=True,
        allow_extra_args=True,
    ),
)
@click.option(
    "--endpoint",
    "-e",
    type=str,
    help="Xinference endpoint.",
)
@click.option(
    "--model-name",
    "-n",
    type=str,
    required=True,
    help="Provide the name of the model to be launched.",
)
@click.option(
    "--model-type",
    "-t",
    type=str,
    default="LLM",
    help="Specify type of model, LLM as default.",
)
@click.option(
    "--model-engine",
    "-en",
    type=str,
    default=None,
    help="Specify the inference engine of the model when launching LLM.",
)
@click.option(
    "--model-uid",
    "-u",
    type=str,
    default=None,
    help="Specify UID of model, default is None.",
)
@click.option(
    "--size-in-billions",
    "-s",
    default=None,
    type=str,
    help="Specify the model size in billions of parameters.",
)
@click.option(
    "--model-format",
    "-f",
    default=None,
    type=str,
    help="Specify the format of the model, e.g. pytorch, ggmlv3, etc.",
)
@click.option(
    "--quantization",
    "-q",
    default=None,
    type=str,
    help="Define the quantization settings for the model.",
)
@click.option(
    "--replica",
    "-r",
    default=1,
    type=int,
    help="The replica count of the model, default is 1.",
)
@click.option(
    "--n-gpu",
    default="auto",
    type=str,
    help='The number of GPUs used by the model, default is "auto".',
)
@click.option(
    "--lora-modules",
    "-lm",
    multiple=True,
    type=(str, str),
    help="LoRA module configurations in the format name=path. Multiple modules can be specified.",
)
@click.option(
    "--image-lora-load-kwargs",
    "-ld",
    "image_lora_load_kwargs",
    type=(str, str),
    multiple=True,
)
@click.option(
    "--image-lora-fuse-kwargs",
    "-fd",
    "image_lora_fuse_kwargs",
    type=(str, str),
    multiple=True,
)
@click.option(
    "--worker-ip",
    default=None,
    type=str,
    help="Specify which worker this model runs on by ip, for distributed situation.",
)
@click.option(
    "--gpu-idx",
    default=None,
    type=str,
    help="Specify which GPUs of a worker this model can run on, separated with commas.",
)
@click.option(
    "--trust-remote-code",
    default=True,
    type=bool,
    help="Whether or not to allow for custom models defined on the Hub in their own modeling files.",
)
@click.option(
    "--api-key",
    "-ak",
    default=None,
    type=str,
    help="Api-Key for access xinference api with authorization.",
)
@click.pass_context
def model_launch(
    ctx,
    endpoint: Optional[str],
    model_name: str,
    model_type: str,
    model_engine: Optional[str],
    model_uid: str,
    size_in_billions: str,
    model_format: str,
    quantization: str,
    replica: int,
    n_gpu: str,
    lora_modules: Optional[Tuple],
    image_lora_load_kwargs: Optional[Tuple],
    image_lora_fuse_kwargs: Optional[Tuple],
    worker_ip: Optional[str],
    gpu_idx: Optional[str],
    trust_remote_code: bool,
    api_key: Optional[str],
):
    kwargs = {}
    for i in range(0, len(ctx.args), 2):
        if not ctx.args[i].startswith("--"):
            raise ValueError("You must specify extra kwargs with `--` prefix.")
        kwargs[ctx.args[i][2:]] = handle_click_args_type(ctx.args[i + 1])
    print(f"Launch model name: {model_name} with kwargs: {kwargs}", file=sys.stderr)

    if model_type == "LLM" and model_engine is None:
        raise ValueError("--model-engine is required for LLM models.")

    if n_gpu.lower() == "none":
        _n_gpu: Optional[Union[int, str]] = None
    elif n_gpu == "auto":
        _n_gpu = n_gpu
    else:
        _n_gpu = int(n_gpu)

    image_lora_load_params = (
        {k: handle_click_args_type(v) for k, v in dict(image_lora_load_kwargs).items()}
        if image_lora_load_kwargs
        else None
    )
    image_lora_fuse_params = (
        {k: handle_click_args_type(v) for k, v in dict(image_lora_fuse_kwargs).items()}
        if image_lora_fuse_kwargs
        else None
    )

    lora_list = (
        [{"lora_name": k, "local_path": v} for k, v in dict(lora_modules).items()]
        if lora_modules
        else []
    )

    peft_model_config = (
        {
            "image_lora_load_kwargs": image_lora_load_params,
            "image_lora_fuse_kwargs": image_lora_fuse_params,
            "lora_list": lora_list,
        }
        if lora_list or image_lora_load_params or image_lora_fuse_params
        else None
    )

    _gpu_idx: Optional[List[int]] = (
        None if gpu_idx is None else [int(idx) for idx in gpu_idx.split(",")]
    )

    endpoint = get_endpoint(endpoint)
    model_size: Optional[Union[str, int]] = (
        size_in_billions
        if size_in_billions is None
        or "_" in size_in_billions
        or "." in size_in_billions
        else int(size_in_billions)
    )
    client = RESTfulClient(base_url=endpoint, api_key=api_key)
    if api_key is None:
        client._set_token(get_stored_token(endpoint, client))

    model_uid = client.launch_model(
        model_name=model_name,
        model_type=model_type,
        model_engine=model_engine,
        model_uid=model_uid,
        model_size_in_billions=model_size,
        model_format=model_format,
        quantization=quantization,
        replica=replica,
        n_gpu=_n_gpu,
        peft_model_config=peft_model_config,
        worker_ip=worker_ip,
        gpu_idx=_gpu_idx,
        trust_remote_code=trust_remote_code,
        **kwargs,
    )

    print(f"Model uid: {model_uid}", file=sys.stderr)


@cli.command(
    "list",
    help="List all running models in Xinference.",
)
@click.option(
    "--endpoint",
    "-e",
    type=str,
    help="Xinference endpoint.",
)
@click.option(
    "--api-key",
    "-ak",
    default=None,
    type=str,
    help="Api-Key for access xinference api with authorization.",
)
def model_list(endpoint: Optional[str], api_key: Optional[str]):
    from tabulate import tabulate

    endpoint = get_endpoint(endpoint)
    client = RESTfulClient(base_url=endpoint, api_key=api_key)
    if api_key is None:
        client._set_token(get_stored_token(endpoint, client))

    llm_table = []
    embedding_table = []
    rerank_table = []
    image_table = []
    audio_table = []
    models = client.list_models()
    for model_uid, model_spec in models.items():
        if model_spec["model_type"] == "LLM":
            llm_table.append(
                [
                    model_uid,
                    model_spec["model_type"],
                    model_spec["model_name"],
                    model_spec["model_format"],
                    model_spec["model_size_in_billions"],
                    model_spec["quantization"],
                ]
            )
        elif model_spec["model_type"] == "embedding":
            embedding_table.append(
                [
                    model_uid,
                    model_spec["model_type"],
                    model_spec["model_name"],
                    model_spec["dimensions"],
                ]
            )
        elif model_spec["model_type"] == "rerank":
            rerank_table.append(
                [model_uid, model_spec["model_type"], model_spec["model_name"]]
            )
        elif model_spec["model_type"] == "image":
            image_table.append(
                [
                    model_uid,
                    model_spec["model_type"],
                    model_spec["model_name"],
                    str(model_spec["controlnet"]),
                ]
            )
        elif model_spec["model_type"] == "audio":
            audio_table.append(
                [model_uid, model_spec["model_type"], model_spec["model_name"]]
            )
    if llm_table:
        print(
            tabulate(
                llm_table,
                headers=[
                    "UID",
                    "Type",
                    "Name",
                    "Format",
                    "Size (in billions)",
                    "Quantization",
                ],
            ),
            file=sys.stderr,
        )
        print()  # add a blank line for better visual experience
    if embedding_table:
        print(
            tabulate(
                embedding_table,
                headers=[
                    "UID",
                    "Type",
                    "Name",
                    "Dimensions",
                ],
            ),
            file=sys.stderr,
        )
        print()
    if rerank_table:
        print(
            tabulate(
                rerank_table,
                headers=["UID", "Type", "Name"],
            ),
            file=sys.stderr,
        )
        print()
    if image_table:
        print(
            tabulate(
                image_table,
                headers=["UID", "Type", "Name", "Controlnet"],
            ),
            file=sys.stderr,
        )
        print()
    if audio_table:
        print(
            tabulate(
                audio_table,
                headers=["UID", "Type", "Name"],
            ),
            file=sys.stderr,
        )
        print()


@cli.command(
    "terminate",
    help="Terminate a deployed model through unique identifier (UID) of the model.",
)
@click.option(
    "--endpoint",
    "-e",
    type=str,
    help="Xinference endpoint.",
)
@click.option(
    "--model-uid",
    type=str,
    required=True,
    help="The unique identifier (UID) of the model.",
)
@click.option(
    "--api-key",
    "-ak",
    default=None,
    type=str,
    help="Api-Key for access xinference api with authorization.",
)
def model_terminate(
    endpoint: Optional[str],
    model_uid: str,
    api_key: Optional[str],
):
    endpoint = get_endpoint(endpoint)
    client = RESTfulClient(base_url=endpoint, api_key=api_key)
    if api_key is None:
        client._set_token(get_stored_token(endpoint, client))
    client.terminate_model(model_uid=model_uid)


@cli.command("generate", help="Generate text using a running LLM.")
@click.option("--endpoint", "-e", type=str, help="Xinference endpoint.")
@click.option(
    "--model-uid",
    type=str,
    help="The unique identifier (UID) of the model.",
)
@click.option(
    "--max_tokens",
    default=512,
    type=int,
    help="Maximum number of tokens in the generated text (default is 512).",
)
@click.option(
    "--stream",
    default=True,
    type=bool,
    help="Whether to stream the generated text. Use 'True' for streaming (default is True).",
)
@click.option(
    "--api-key",
    "-ak",
    default=None,
    type=str,
    help="Api-Key for access xinference api with authorization.",
)
def model_generate(
    endpoint: Optional[str],
    model_uid: str,
    max_tokens: int,
    stream: bool,
    api_key: Optional[str],
):
    endpoint = get_endpoint(endpoint)
    client = RESTfulClient(base_url=endpoint, api_key=api_key)
    if api_key is None:
        client._set_token(get_stored_token(endpoint, client))
    if stream:
        # TODO: when stream=True, RestfulClient cannot generate words one by one.
        # So use Client in temporary. The implementation needs to be changed to
        # RestfulClient in the future.
        async def generate_internal():
            while True:
                # the prompt will be written to stdout.
                # https://docs.python.org/3.10/library/functions.html#input
                prompt = input("Prompt: ")
                if prompt == "":
                    break
                print(f"Completion: {prompt}", end="", file=sys.stdout)
                for chunk in model.generate(
                    prompt=prompt,
                    generate_config={"stream": stream, "max_tokens": max_tokens},
                ):
                    choice = chunk["choices"][0]
                    if "text" not in choice:
                        continue
                    else:
                        print(choice["text"], end="", flush=True, file=sys.stdout)
                print("", file=sys.stdout)

        model = client.get_model(model_uid=model_uid)

        loop = asyncio.get_event_loop()
        coro = generate_internal()

        if loop.is_running():
            isolation = Isolation(asyncio.new_event_loop(), threaded=True)
            isolation.start()
            isolation.call(coro)
        else:
            task = loop.create_task(coro)
            try:
                loop.run_until_complete(task)
            except KeyboardInterrupt:
                task.cancel()
                loop.run_until_complete(task)
                # avoid displaying exception-unhandled warnings
                task.exception()
    else:
        restful_model = client.get_model(model_uid=model_uid)
        if not isinstance(
            restful_model, (RESTfulChatModelHandle, RESTfulGenerateModelHandle)
        ):
            raise ValueError(f"model {model_uid} has no generate method")

        while True:
            prompt = input("User: ")
            if prompt == "":
                break
            print(f"Assistant: {prompt}", end="", file=sys.stdout)
            response = restful_model.generate(
                prompt=prompt,
                generate_config={"stream": stream, "max_tokens": max_tokens},
            )
            if not isinstance(response, dict):
                raise ValueError("generate result is not valid")
            print(f"{response['choices'][0]['text']}\n", file=sys.stdout)


@cli.command("chat", help="Chat with a running LLM.")
@click.option("--endpoint", "-e", type=str, help="Xinference endpoint.")
@click.option("--model-uid", type=str, help="The unique identifier (UID) of the model.")
@click.option(
    "--max_tokens",
    default=512,
    type=int,
    help="Maximum number of tokens in each message (default is 512).",
)
@click.option(
    "--stream",
    default=True,
    type=bool,
    help="Whether to stream the chat messages. Use 'True' for streaming (default is True).",
)
@click.option(
    "--api-key",
    "-ak",
    default=None,
    type=str,
    help="Api-Key for access xinference api with authorization.",
)
def model_chat(
    endpoint: Optional[str],
    model_uid: str,
    max_tokens: int,
    stream: bool,
    api_key: Optional[str],
):
    # TODO: chat model roles may not be user and assistant.
    endpoint = get_endpoint(endpoint)
    client = RESTfulClient(base_url=endpoint, api_key=api_key)
    if api_key is None:
        client._set_token(get_stored_token(endpoint, client))

    chat_history: "List[ChatCompletionMessage]" = []
    if stream:
        # TODO: when stream=True, RestfulClient cannot generate words one by one.
        # So use Client in temporary. The implementation needs to be changed to
        # RestfulClient in the future.
        async def chat_internal():
            while True:
                # the prompt will be written to stdout.
                # https://docs.python.org/3.10/library/functions.html#input
                prompt = input("User: ")
                if prompt == "":
                    break
                print("Assistant: ", end="", file=sys.stdout)
                response_content = ""
                for chunk in model.chat(
                    prompt=prompt,
                    chat_history=chat_history,
                    generate_config={"stream": stream, "max_tokens": max_tokens},
                ):
                    delta = chunk["choices"][0]["delta"]
                    if "content" not in delta:
                        continue
                    else:
                        response_content += delta["content"]
                        print(delta["content"], end="", flush=True, file=sys.stdout)
                print("", file=sys.stdout)
                chat_history.append(ChatCompletionMessage(role="user", content=prompt))
                chat_history.append(
                    ChatCompletionMessage(role="assistant", content=response_content)
                )

        model = client.get_model(model_uid=model_uid)

        loop = asyncio.get_event_loop()
        coro = chat_internal()

        if loop.is_running():
            isolation = Isolation(asyncio.new_event_loop(), threaded=True)
            isolation.start()
            isolation.call(coro)
        else:
            task = loop.create_task(coro)
            try:
                loop.run_until_complete(task)
            except KeyboardInterrupt:
                task.cancel()
                loop.run_until_complete(task)
                # avoid displaying exception-unhandled warnings
                task.exception()
    else:
        restful_model = client.get_model(model_uid=model_uid)
        if not isinstance(
            restful_model, (RESTfulChatModelHandle, RESTfulChatglmCppChatModelHandle)
        ):
            raise ValueError(f"model {model_uid} has no chat method")

        while True:
            prompt = input("User: ")
            if prompt == "":
                break
            chat_history.append(ChatCompletionMessage(role="user", content=prompt))
            print("Assistant: ", end="", file=sys.stdout)
            response = restful_model.chat(
                prompt=prompt,
                chat_history=chat_history,
                generate_config={"stream": stream, "max_tokens": max_tokens},
            )
            if not isinstance(response, dict):
                raise ValueError("chat result is not valid")
            response_content = response["choices"][0]["message"]["content"]
            print(f"{response_content}\n", file=sys.stdout)
            chat_history.append(
                ChatCompletionMessage(role="assistant", content=response_content)
            )


@cli.command("vllm-models", help="Query and display models compatible with vLLM.")
@click.option("--endpoint", "-e", type=str, help="Xinference endpoint.")
@click.option(
    "--api-key",
    "-ak",
    default=None,
    type=str,
    help="Api-Key for access xinference api with authorization.",
)
def vllm_models(endpoint: Optional[str], api_key: Optional[str]):
    endpoint = get_endpoint(endpoint)
    client = RESTfulClient(base_url=endpoint, api_key=api_key)
    if api_key is None:
        client._set_token(get_stored_token(endpoint, client))
    vllm_models_dict = client.vllm_models()
    print("VLLM supported model families:")
    chat_models = vllm_models_dict["chat"]
    supported_models = vllm_models_dict["generate"]

    print("VLLM supported chat model families:", chat_models)
    print("VLLM supported generate model families:", supported_models)


@cli.command("login", help="Login when the cluster is authenticated.")
@click.option("--endpoint", "-e", type=str, help="Xinference endpoint.")
@click.option("--username", type=str, required=True, help="Username.")
@click.option(
    "--password",
    type=str,
    required=True,
    help="Password.",
)
def cluster_login(
    endpoint: Optional[str],
    username: str,
    password: str,
):
    endpoint = get_endpoint(endpoint)
    restful_client = RESTfulClient(base_url=endpoint)
    if restful_client._cluster_authed:
        restful_client.login(username, password)
        access_token = restful_client._get_token()
        assert access_token is not None
        os.makedirs(XINFERENCE_AUTH_DIR, exist_ok=True)
        hashed_ep = get_hash_endpoint(endpoint)
        with open(os.path.join(XINFERENCE_AUTH_DIR, hashed_ep), "w") as f:
            f.write(access_token)


@cli.command(name="engine", help="Query engine parameters by model name.")
@click.option("--model-name", type=str, required=True, help="Model name.")
@click.option("--model_engine", type=str, default=None, help="Model engine.")
@click.option("--model_format", type=str, default=None, help="Model format.")
@click.option(
    "--model_size_in_billions", type=str, default=None, help="Model size in billions."
)
@click.option("--quantization", type=str, default=None, help="Quantization.")
@click.option("--endpoint", "-e", type=str, help="Xinference endpoint.")
@click.option(
    "--api-key",
    "-ak",
    default=None,
    type=str,
    help="Api-Key for access xinference api with authorization.",
)
def query_engine_by_model_name(
    model_name: str,
    model_engine: Optional[str],
    model_format: Optional[str],
    model_size_in_billions: Optional[Union[str, int]],
    quantization: Optional[str],
    endpoint: Optional[str],
    api_key: Optional[str],
):
    from tabulate import tabulate

    endpoint = get_endpoint(endpoint)
    client = RESTfulClient(base_url=endpoint, api_key=api_key)
    if api_key is None:
        client._set_token(get_stored_token(endpoint, client))

    llm_engines = client.query_engine_by_model_name(model_name)
    if model_engine is not None and model_engine not in llm_engines:
        raise ValueError(f"Model {model_name} cannot be run on engine {model_engine}.")

    table = []
    engines = [model_engine] if model_engine is not None else list(llm_engines.keys())
    for engine in engines:
        params = llm_engines[engine]
        for param in params:
            if (
                (model_format is None or model_format == param["model_format"])
                and (
                    model_size_in_billions is None
                    or model_size_in_billions == str(param["model_size_in_billions"])
                )
                and (quantization is None or quantization in param["quantizations"])
            ):
                if quantization is not None:
                    table.append(
                        [
                            model_name,
                            engine,
                            param["model_format"],
                            param["model_size_in_billions"],
                            quantization,
                        ]
                    )
                else:
                    for quant in param["quantizations"]:
                        table.append(
                            [
                                model_name,
                                engine,
                                param["model_format"],
                                param["model_size_in_billions"],
                                quant,
                            ]
                        )
    if len(table) == 0:
        raise ValueError(
            f"Cannot find parameters for Model {model_name} with format {model_format}, size {model_size_in_billions} and quantization {quantization} on engine {model_engine}."
        )
    else:
        print(
            tabulate(
                table,
                headers=[
                    "Name",
                    "Engine",
                    "Format",
                    "Size (in billions)",
                    "Quantization",
                ],
            ),
            file=sys.stderr,
        )


if __name__ == "__main__":
    cli()
