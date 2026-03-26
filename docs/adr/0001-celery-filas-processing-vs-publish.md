# ADR-0001: Duas filas Celery — `processing` e `publish`

- **Data:** 2026-03-26  
- **Estado:** Aceite  
- **Contexto:** Plataforma com trabalho CPU/GPU pesado (FFmpeg, Whisper, transcrição em chunks) e trabalho I/O e sensível ao tempo (verificar posts agendados, chamar APIs de redes, reconciliar estado no YouTube).

## Problema

Se todas as tarefas Celery partilharem uma única fila e um conjunto limitado de workers:

- Uma transcrição longa ou um *render* pode **saturar** os workers durante minutos ou horas.
- Tarefas de **publicação** e **agendamento** (que devem correr em janelas previsíveis) ficam **atrás** na fila, atrasando posts ou verificações.

## Decisão

1. **Fila `processing` (padrão)** — `CELERY_TASK_DEFAULT_QUEUE = "processing"`.  
   Destinos: `process_job`, legendas, *auto cuts* (`analyze_auto_cuts_task`, `finalizar_auto_cut_task`), e limpezas pesadas quando aplicável.

2. **Fila `publish`** — roteamento explícito em `CELERY_TASK_ROUTES` em `social_automation/settings.py`.  
   Destinos: `check_scheduled_posts_task`, `post_to_platforms_task`, `generate_daily_factory_schedules_task`, `reconcile_youtube_schedules_task`, *upload* de thumbnails pós-lote, filas de marca, etc.

3. **Operação:** em produção esperam-se **dois (ou mais) workers** — pelo menos um consumidor `-Q processing` e outro `-Q publish` (ver README / scripts `.bat`).

## Consequências

**Positivas**

- Publicação e agendamento **não competem** diretamente com jobs de render/transcrição no mesmo pool de workers, se estes forem separados.
- Facilita **escalar** só a fila que está sob pressão (mais workers de *processing* vs mais de *publish*).

**Negativas / custos**

- Infraestrutura e operação **um pouco mais complexas** (dois comandos de worker, monitorização por fila).
- Se um único worker consumir **ambas** as filas sem separação de capacidade, o benefício reduz-se — a decisão assume **capacidade separada ou priorização** na exploração.

## Alternativas consideradas

- **Uma fila só + prioridades Celery** — possível, mas mais frágil quando o volume de tarefas pesadas é alto e imprevisível.
- **Filas por cliente** — útil em multi-tenant estrito; aqui o *tenant* lógico é *Factory/Brand* dentro do mesmo deploy.

## Referências no código

- `social_automation/settings.py` — `CELERY_TASK_DEFAULT_QUEUE`, `CELERY_TASK_ROUTES`
- `config/celery.py` — *beat schedule* das tasks periódicas
