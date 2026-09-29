# finance-verifier (합성 픽스처)

## 스택
- Python 3.12
- `verifier-core`: 결정 모델 (현재 자체 규칙 + LLM judge)
- `serving-layer`: vLLM
- `eval-harness`: Held-out 53건, 결과는 `eval/results/*.json`

## 의존성 → component
- `vllm` → serving-layer
