# models 文件夹说明

这个文件夹存放**本地大模型与推理引擎**,体积较大,默认已被 `.gitignore` 排除,
不会上传到 GitHub —— 每位用户按下面的指引自行下载即可。

```
models/
├── gguf/    ← 放你下载的 .gguf 大模型文件(如 qwen2.5-0.5b-instruct-q4_k_m.gguf)
├── llama/   ← 放 llama.cpp 的 CPU 版 llama-server.exe(及其同级 dll)
└── llama/cuda/ ← 有 NVIDIA 显卡时: 放 CUDA 版 llama-server.exe + cudart 运行库
```

## 一、下载 GGUF 模型(选一个即可)

| 模型 | 大小 | 说明 |
| --- | --- | --- |
| Qwen2.5-0.5B-Instruct | ~0.4 GB | 新手跑通流程,速度快 |
| Qwen2.5-1.5B-Instruct | ~1.1 GB | 轻量但回答质量明显更好 |
| Qwen2.5-7B-Instruct | ~4.7 GB | 质量好,显存/内存充裕时推荐 |

国内下载(任选):

- HuggingFace 镜像: https://hf-mirror.com/Qwen/Qwen2.5-0.5B-Instruct-GGUF
- ModelScope 魔搭: https://modelscope.cn/models (搜 GGUF)
- 命令行(hf 镜像):
  ```bash
  python -c "from huggingface_hub import hf_hub_download; \
  hf_hub_download('Qwen/Qwen2.5-0.5B-Instruct-GGUF', \
  'qwen2.5-0.5b-instruct-q4_k_m.gguf', local_dir=r'models/gguf')"
  ```

## 二、下载 llama.cpp 推理引擎(免编译)

到官方发布页选**最新 bXXXX 版本**: https://github.com/ggml-org/llama.cpp/releases

- **没有 NVIDIA 显卡**: 下载 `llama-bXXXX-bin-win-cpu-x64.zip`(约 17MB),
  解压后把 `llama-server.exe` 及同级 dll 放进 `models/llama/`;
- **有 NVIDIA 显卡(CUDA 加速)**: 下载
  `llama-bXXXX-bin-win-cuda-13.x-x64.zip` **和** `cudart-llama-bin-win-cuda-13.x-x64.zip`
  两个包,都解压到 `models/llama/cuda/`(cudart 是 CUDA 运行库,缺了会静默退回 CPU)。

> 国内网络下载慢时,可在 GitHub 地址前加代理前缀(社区镜像,可能变动):
> `https://ghfast.top/` 或 `https://gh-proxy.com/` 或 `https://ghproxy.net/`

## 三、在应用中使用

1. 启动 GreenRAG →「设置 → 大模型」切到 **本地 GGUF 模型**;
2. 点「↻ 刷新」选择模型文件,按显卡显存调节 **GPU 卸载层数**(0=纯CPU,99=全部进显存);
3. 点「▶ 启动模型」,然后正常提问即可 —— 回答依旧带「文档 + 页码」引用标注。

顶栏的 GPU「灵动岛」会实时显示显卡占用/显存/温度,以及引擎是否在运行。
