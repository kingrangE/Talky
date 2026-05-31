---
license: apache-2.0
license_link: https://huggingface.co/skt/A.X-4.0-Light/blob/main/LICENSE
language:
- en
- ko
pipeline_tag: text-generation
library_name: transformers
model_id: skt/A.X-4.0-Light
developers: SKT AI Model Lab
base_model: skt/A.X-4.0-Light
tags:
- llama-cpp
- gguf-my-repo
model-index:
- name: A.X-4.0-Light
  results:
  - task:
      type: generate_until
      name: mmlu
    dataset:
      name: mmlu (chat CoT)
      type: hails/mmlu_no_train
    metrics:
    - type: exact_match
      value: 75.43
      name: exact_match
  - task:
      type: generate_until
      name: kmmlu
    dataset:
      name: kmmlu (chat CoT)
      type: HAERAE-HUB/KMMLU
    metrics:
    - type: exact_match
      value: 64.15
      name: exact_match
---

# jayusop/A.X-4.0-Light-Q4_K_M-GGUF
This model was converted to GGUF format from [`skt/A.X-4.0-Light`](https://huggingface.co/skt/A.X-4.0-Light) using llama.cpp via the ggml.ai's [GGUF-my-repo](https://huggingface.co/spaces/ggml-org/gguf-my-repo) space.
Refer to the [original model card](https://huggingface.co/skt/A.X-4.0-Light) for more details on the model.

## Use with llama.cpp
Install llama.cpp through brew (works on Mac and Linux)

```bash
brew install llama.cpp

```
Invoke the llama.cpp server or the CLI.

### CLI:
```bash
llama-cli --hf-repo jayusop/A.X-4.0-Light-Q4_K_M-GGUF --hf-file a.x-4.0-light-q4_k_m.gguf -p "The meaning to life and the universe is"
```

### Server:
```bash
llama-server --hf-repo jayusop/A.X-4.0-Light-Q4_K_M-GGUF --hf-file a.x-4.0-light-q4_k_m.gguf -c 2048
```

Note: You can also use this checkpoint directly through the [usage steps](https://github.com/ggerganov/llama.cpp?tab=readme-ov-file#usage) listed in the Llama.cpp repo as well.

Step 1: Clone llama.cpp from GitHub.
```
git clone https://github.com/ggerganov/llama.cpp
```

Step 2: Move into the llama.cpp folder and build it with `LLAMA_CURL=1` flag along with other hardware-specific flags (for ex: LLAMA_CUDA=1 for Nvidia GPUs on Linux).
```
cd llama.cpp && LLAMA_CURL=1 make
```

Step 3: Run inference through the main binary.
```
./llama-cli --hf-repo jayusop/A.X-4.0-Light-Q4_K_M-GGUF --hf-file a.x-4.0-light-q4_k_m.gguf -p "The meaning to life and the universe is"
```
or 
```
./llama-server --hf-repo jayusop/A.X-4.0-Light-Q4_K_M-GGUF --hf-file a.x-4.0-light-q4_k_m.gguf -c 2048
```
