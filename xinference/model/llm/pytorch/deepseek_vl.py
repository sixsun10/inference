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
import base64
import logging
import os.path
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

import requests
import torch

from ....model.utils import select_device
from ....types import (
    ChatCompletion,
    ChatCompletionChoice,
    ChatCompletionChunk,
    ChatCompletionMessage,
    CompletionUsage,
)
from ..llm_family import LLMFamilyV1, LLMSpecV1
from .core import PytorchChatModel, PytorchGenerateConfig

logger = logging.getLogger(__name__)


class DeepSeekVLChatModel(PytorchChatModel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tokenizer = None
        self._model = None
        self._vl_chat_processor = None
        self._type = None

    @classmethod
    def match(
        cls, model_family: "LLMFamilyV1", model_spec: "LLMSpecV1", quantization: str
    ) -> bool:
        if "deepseek" in model_family.model_name:
            return True
        return False

    def load(self):
        from transformers import AutoModelForCausalLM

        from ....thirdparty.deepseek_vl.models import (
            MultiModalityCausalLM,
            VLChatProcessor,
        )

        self._device = self._pytorch_model_config.get("device", "auto")
        self._device = select_device(self._device)
        self._type = torch.float16 if self._device == "mps" else torch.bfloat16

        # specify the path to the model
        self._vl_chat_processor: VLChatProcessor = VLChatProcessor.from_pretrained(  # type: ignore
            self.model_path
        )
        self._tokenizer = self._vl_chat_processor.tokenizer

        vl_gpt: MultiModalityCausalLM = AutoModelForCausalLM.from_pretrained(  # type: ignore
            self.model_path, trust_remote_code=True, device_map=self._device
        )
        self._model = vl_gpt.to(self._type).eval()

    @staticmethod
    def _message_content_to_deepseek(content) -> Tuple[str, List[str]]:
        def _ensure_url(_url):
            if _url.startswith("data:"):
                logging.info("Parse url by base64 decoder.")
                # https://platform.openai.com/docs/guides/vision/uploading-base-64-encoded-images
                # e.g. f"data:image/jpeg;base64,{base64_image}"
                _type, data = _url.split(";")
                _, ext = _type.split("/")
                data = data[len("base64,") :]
                data = base64.b64decode(data.encode("utf-8"))

                with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
                    f.write(data)
                logging.info("Dump base64 data to %s", f.name)
                return f.name
            else:
                if len(_url) > 2048:
                    raise Exception(f"Image url is too long, {len(_url)} > 2048.")

                return _url

        def _download(_images):
            local_images = []

            # To make requests.get works
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            }
            with ThreadPoolExecutor() as executor:
                for url in images:
                    try:
                        if os.path.exists(url):
                            local_images.append(url)
                            continue
                    except Exception as e:
                        logger.debug("Image is remote: %s, e: %s", url, e)
                        pass
                    # Append a placeholder
                    local_images.append(None)

                    def _fill_placeholder(_url, _index):
                        response = requests.get(url, headers=headers)
                        local_images[_index] = BytesIO(response.content)

                    executor.submit(_fill_placeholder, url, len(local_images) - 1)
            return local_images

        if not isinstance(content, str):
            # TODO(codingl2k1): Optimize _ensure_url

            images = []
            new_content = []
            for c in content:
                c_type = c.get("type")
                if c_type == "image_url":
                    images.append(_ensure_url(c["image_url"]["url"]))
                elif c_type == "text":
                    new_content.append(c["text"])
            if images:
                new_content.insert(0, "<image_placeholder>")
                images = _download(images)
            return "".join(new_content), images
        return content, []

    def chat(
        self,
        prompt: Union[str, List[Dict]],
        system_prompt: Optional[str] = None,
        chat_history: Optional[List[ChatCompletionMessage]] = None,
        generate_config: Optional[PytorchGenerateConfig] = None,
    ) -> Union[ChatCompletion, Iterator[ChatCompletionChunk]]:
        if generate_config and generate_config.get("stream"):
            raise Exception(
                f"Chat with model {self.model_family.model_name} does not support stream."
            )
        prompt, images = self._message_content_to_deepseek(prompt)
        prompt_messages: List[Dict[str, Any]] = [
            {
                "role": "User",
                "content": prompt,
            },
            {"role": "Assistant", "content": ""},
        ]
        if images:
            prompt_messages[0]["images"] = images

        # Convert openai history to qwen vl history
        deepseek_history = []
        for h in chat_history or []:
            role = h["role"]
            if role == "user":
                content, images = self._message_content_to_deepseek(h["content"])
                msg: Dict[str, Any] = {
                    "role": "User",
                    "content": content,
                }
                if images:
                    msg["images"] = images
                deepseek_history.append(msg)
            elif role == "assistant":
                deepseek_history.append({"role": "Assistant", "content": h["content"]})
            else:
                logger.error("Unexpected msg in chat history: %s", h)

        deepseek_history.extend(prompt_messages)

        from ....thirdparty.deepseek_vl.utils.io import load_pil_images

        # load images and prepare for inputs
        pil_images = load_pil_images(deepseek_history)
        prepare_inputs = self._vl_chat_processor(
            conversations=deepseek_history, images=pil_images, force_batchify=True
        ).to(self._model.device, self._model.dtype)

        # run image encoder to get the image embeddings
        inputs_embeds = self._model.prepare_inputs_embeds(**prepare_inputs)

        # run the model to get the response
        outputs = self._model.language_model.generate(
            inputs_embeds=inputs_embeds,
            attention_mask=prepare_inputs.attention_mask,
            pad_token_id=self._tokenizer.eos_token_id,
            bos_token_id=self._tokenizer.bos_token_id,
            eos_token_id=self._tokenizer.eos_token_id,
            max_new_tokens=512,
            do_sample=True,
            top_p=0.95,
            temperature=0.2,
            repetition_penalty=1.1,
            use_cache=True,
        )

        answer = self._tokenizer.decode(
            outputs[0].cpu().tolist(), skip_special_tokens=True
        )

        return ChatCompletion(
            id="chat" + str(uuid.uuid1()),
            object="chat.completion",
            created=int(time.time()),
            model=self.model_uid,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message={"role": "assistant", "content": answer},
                    finish_reason="stop",
                )
            ],
            usage=CompletionUsage(
                prompt_tokens=-1, completion_tokens=-1, total_tokens=-1
            ),
        )
