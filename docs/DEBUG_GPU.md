# Debug: Crash/Travamento na GPU (Whisper)

Quando a transcrição trava com GPU mas funciona com CPU, siga estes passos para diagnosticar.

## Variáveis de ambiente (.env)

| Variável | Valores | Descrição |
|----------|---------|-----------|
| `WHISPER_DEVICE` | `cpu` | Força CPU (evita crash em máquinas com pouca VRAM) |
| `WHISPER_DEVICE` | `cuda` | Força GPU; se falhar, faz fallback para CPU |
| `WHISPER_DEVICE` | (vazio) | Tenta CUDA primeiro, fallback para CPU em erro |
| `WHISPER_DEBUG_GPU` | `1` | **Modo diagnóstico**: não faz fallback; relança exceção com traceback completo |
| `WHISPER_MODEL` | `tiny`, `base`, `small`, `medium`, `large-v3` | Modelo Whisper. Vídeos curtos: default `large-v3`. Vídeos longos (chunked): default `small`. |

Para **rodar com GPU e ver os erros** no Celery:
1. Remova ou comente `WHISPER_DEVICE=cpu` no `.env` (ou defina `WHISPER_DEVICE=cuda`)
2. Adicione `WHISPER_DEBUG_GPU=1` para não fazer fallback e ver o erro completo
3. Reinicie o worker Celery e acompanhe os logs

## 1. Teste isolado (fora do Celery)

Rode a transcrição **sem** Celery para ver se o problema é específico do worker:

```powershell
# Com GPU (vai travar se o problema for na transcrição)
python manage.py test_whisper_gpu storage/media/cortes_processo/1/chunk_001.m4a --device cuda

# Com CPU (deve funcionar)
python manage.py test_whisper_gpu storage/media/cortes_processo/1/chunk_001.m4a --device cpu
```

Use um arquivo de áudio real (ex: um chunk de 10–18 min). Se tiver um job recente, os chunks estão em `storage/media/cortes_processo/<analysis_id>/`.

**Interpretação:**
- Trava no teste isolado com GPU → problema no faster-whisper/CUDA
- Funciona no teste isolado com GPU → problema provável no Celery + CUDA

## 2. Logs para localizar o travamento

Os logs indicam em que etapa parou:

- `Whisper: carregando modelo X em CUDA...` → trava no carregamento
- `Whisper: modelo carregado. Iniciando transcrição...` → trava na transcrição

Com as alterações de logging, o erro é registrado com traceback completo antes do fallback.

## 3. CUDA síncrono (melhor stack trace)

Para obter um stack trace mais claro em caso de erro:

```powershell
$env:CUDA_LAUNCH_BLOCKING = "1"
python manage.py test_whisper_gpu caminho/arquivo.m4a --device cuda
```

## 4. py-spy (stack trace com processo travado)

Se o processo travar sem erro:

1. Em outro terminal, descubra o PID do processo Python.
2. Instale: `pip install py-spy`
3. Execute: `py-spy dump --pid <PID>`

Isso mostra em qual função o processo está parado.

## 5. Verificar uso da GPU

Enquanto o processo roda (ou trava):

```powershell
nvidia-smi
```

Confira se há outros processos usando a GPU.

## 6. Alternativas

- Usar CPU: `WHISPER_DEVICE=cpu` no `.env`
- Usar modelo menor: `--model small` ou `medium` no teste
- Atualizar driver NVIDIA e bibliotecas CUDA
