# Feature — Modelos de capa múltiplos, com texto na lateral direita

| | |
|---|---|
| **Início** | 2026-09-03 |
| **Base** | `develop` (`ef883cc`) |
| **Escopo** | geração de thumbnail dos cortes (`apps/auto_cuts/services/thumbnail.py`), assets de marca, formulário de criação de cortes |
| **Exige parada de produção** | não |
| **Referências visuais** | `docs/cortes_sala_modelo.png` (modelo em branco), `docs/cortes_sala_exemplo.png` (mesmo modelo já preenchido) |

---

## ⏸ PONTO DE RETOMADA

**Estado: plano escrito, nada implementado.** Começar pelo PR 1.

---

## 1. O que o usuário pediu

> "Na pasta docs tem o `cortes_sala_exemplo`, que é um exemplo já preenchido com o texto, e o
> outro é o modelo pronto para usar no app. Precisamos ajustar de forma que eu consiga subir
> esse modelo de capa e o texto que já criamos nas capas seja escrito ali do lado direito,
> como no exemplo. O modelo vai sem texto — nosso trabalho será apenas pôr o texto do lado
> direito. Temos que poder ter mais de 1 modelo e, na criação automática de cortes, ter a
> opção de escolher o modelo de capa. Acredito que já temos essa parte do modelo de capa,
> mas antes era 1 só, não podia escolher, e o posicionamento do texto era embaixo. Agora,
> quando usado um modelo, o texto vai na parte direita como no exemplo; quando não tiver
> modelo, usa o fallback que já faz."

Traduzindo em três mudanças:

1. **Vários modelos por marca** (hoje o app apaga o anterior a cada upload).
2. **Escolher o modelo** no formulário de criação de cortes (hoje é implícito: pega o primeiro do banco).
3. **Texto na zona direita quando há modelo**; faixa inferior vermelha continua sendo o fallback sem modelo.

### Decisões do usuário (2026-09-03)

| Pergunta | Resposta |
| --- | --- |
| Um modelo só ou separado por formato? | **Separado**: um seletor para shorts (9:16) e outro para longs (16:9) |
| A zona do texto é fixa ou configurável? | **Configurável por modelo**, em %, com padrão já preenchido no lado direito |
| Gerar hierarquia de texto (título grande + subtítulos, como no exemplo)? | **Não** — só o título que já geramos hoje. Os subtítulos do exemplo foram escritos à mão |

---

## 2. Como funciona hoje (levantado antes de mexer)

### O modelo de capa já existe — mas é um por marca e não se escolhe

`apps/brands/models.py:485` — `BrandAsset.ASSET_TYPES` já tem `THUMB_SHORT` ("Thumb Shorts")
e `THUMB_LONG` ("Thumb Longs"). `unique_together = ("brand", "asset_type", "label")` — ou
seja, **o banco já permite vários**, com rótulos diferentes.

O que impede ter vários é o **frontend**: `frontend/src/pages/IntroOutro.jsx:509` apaga
todos os assets do mesmo tipo antes de subir o novo:

```js
if (assetType === 'THUMB_SHORT' || assetType === 'THUMB_LONG') {
  const existing = assets.filter((a) => a.asset_type === assetType)
  for (const a of existing) { await deleteBrandAsset(a.id) }
}
```

E o que impede escolher é o **backend**: `apps/auto_cuts/services/thumbnail.py:371-376`
pega o primeiro por `id`:

```python
thumb_asset_type = "THUMB_SHORT" if is_short else "THUMB_LONG"
thumb_asset = BrandAsset.objects.filter(brand=brand, asset_type=thumb_asset_type).order_by("id").first()
```

### Como a capa é montada hoje

`generate_auto_thumbnail(corte, target_brand=None)` (`thumbnail.py:194`):

1. Extrai um frame do vídeo (com fallback de ~5 s dentro do corte).
2. Se for short e o frame for 16:9, recorta o centro para 9:16.
3. Cola o **logo** da marca no canto superior esquerdo.
4. Se houver asset `THUMB_SHORT`/`THUMB_LONG`, redimensiona para `w×h` e faz
   `Image.alpha_composite` por cima do frame.
5. **Sem modelo:** desenha uma faixa sólida (`band_color`) nos 20 % de baixo.
6. Em ambos os casos, escreve o texto **centrado na faixa inferior** (`rect_y1..h`), com
   contorno (`stroke_width ≈ 8 % do corpo`).

O passo 6 é o problema: com o modelo do `docs/cortes_sala_modelo.png` — que é **opaco**, arte
de ponta a ponta — o texto cai em cima do rodapé da arte, e não na área amarela vazia da direita.
O contorno claro (`#FFEBDC`) também briga com a arte.

Observação: como o modelo é opaco, o `alpha_composite` do passo 4 já esconde o frame e o
logo do passo 3. Não é bug — é o comportamento esperado quando a arte cobre tudo.

### Quem chama a geração

`apps/auto_cuts/services/analysis_flow.py` — linhas 1217, 1301, 1527, 1583, sempre com
`target_brand=` (roteamento por tema). **A marca do corte pode não ser a marca do job.**

### Onde nascem as análises (4 caminhos)

| Caminho | Arquivo | Tem formulário? |
| --- | --- | --- |
| Criação manual de cortes | `apps/api/views/auto_cuts.py:242` | sim (`CortesAutomaticos.jsx`) |
| Cortes prontos (lote) | `apps/api/views/auto_cuts.py:342` | sim (modal "cortes prontos") |
| Auto-fetch da Factory | `apps/jobs/tasks_auto_fetch.py:236` | **não** — precisa de padrão por marca |
| Multiple Creator | `apps/multiple_creator/tasks.py:260` | herda do `MultipleCreatorJob` |

### O padrão a copiar: `long_overlay`

`long_overlay_enabled` + `long_overlay_asset` (FK para `BrandAsset`) em `AutoCutAnalysis`
(`apps/auto_cuts/models.py:176-188`), validados em `apps/api/views/auto_cuts.py:184-205` e
`297-318`, expostos no serializer (`apps/api/serializers/auto_cuts.py:144`) e escolhidos na
UI (`CortesAutomaticos.jsx:1368-1396`). **É exatamente a mesma forma** — os FKs de modelo de
capa devem seguir esse desenho, endpoint por endpoint.

---

## 3. Desenho

### 3.1 A zona de texto vira dado do modelo, não do código

Cada modelo carrega sua própria caixa de texto, em **percentagem** da imagem — assim um
modelo com o vazio à direita, outro com o vazio embaixo e outro com o vazio à esquerda
convivem sem tocar no código.

Campos novos em `BrandAsset` (só fazem sentido para `THUMB_SHORT`/`THUMB_LONG`; ignorados no resto):

| Campo | Tipo | Padrão | Para quê |
| --- | --- | --- | --- |
| `text_zone_x` | `PositiveSmallIntegerField` (%) | `60` | borda esquerda da caixa |
| `text_zone_y` | `PositiveSmallIntegerField` (%) | `8` | topo da caixa |
| `text_zone_w` | `PositiveSmallIntegerField` (%) | `38` | largura da caixa |
| `text_zone_h` | `PositiveSmallIntegerField` (%) | `84` | altura da caixa |
| `text_align` | `CharField` `left/center/right` | `center` | alinhamento horizontal das linhas |
| `text_valign` | `CharField` `top/middle/bottom` | `middle` | onde o bloco encosta na caixa |
| `text_color` | `CharField(7)` vazio | `""` | cor do texto; vazio = cor da marca |
| `stroke_enabled` | `BooleanField` | `False` | **contorno desligado por padrão** — o exemplo não tem |
| `stroke_color` | `CharField(7)` vazio | `""` | cor do contorno; vazio = cor da marca |
| `font` | `CharField(16)` vazio | `""` | fonte; vazio = fonte da marca (`anton/bebas/montserrat/impact`) |

Os padrões `60/8/38/84` vêm de medir `docs/cortes_sala_modelo.png`: a diagonal escura termina
no máximo em **60,3 %** da largura, e no exemplo o texto ocupa de ~55 % a ~96,5 % em x e
~10,7 % em y para baixo. Ou seja: quem subir um modelo parecido com esse não precisa mexer em nada.

**Invariantes validados no serializer:** `1 ≤ w,h ≤ 100`, `0 ≤ x,y ≤ 99`, `x+w ≤ 100`, `y+h ≤ 100`.

### 3.2 Escolha do modelo no job

Em `AutoCutAnalysis`:

- `thumb_template_short` → FK `BrandAsset`, `SET_NULL`, `null=True`, `blank=True`, `related_name="+"`
- `thumb_template_long` → FK `BrandAsset`, idem

**`MultipleCreatorJob` fica de fora** (decidido na execução do PR 1, corrigindo o plano original):
o job faz fan-out para várias marcas e um `BrandAsset` pertence a uma marca só, então um FK no
job valeria para uma marca e seria silenciosamente ignorado nas outras (§3.3, passo 1). A
granularidade certa ali é o **padrão por marca**, que cada análise filha resolve sozinha.

Em `Brand` (para os jobs que nascem sem formulário — auto-fetch da Factory):

- `default_thumb_template_short`, `default_thumb_template_long` → FK `"BrandAsset"` (referência por string, `SET_NULL`, `related_name="+"`)

Não há flag `enabled`: **FK nulo = sem modelo = fallback**. Uma coisa a menos para desincronizar.

### 3.3 Resolução do modelo na hora de desenhar

A marca do corte (`target_brand`) pode ser diferente da marca do job. Ordem:

```
1. analysis.thumb_template_{short,long}  — se o asset pertencer à marca do corte
2. brand.default_thumb_template_{short,long}
3. primeiro BrandAsset THUMB_* da marca por id   ← compatibilidade com o que existe hoje
4. nenhum → fallback (faixa inferior de 20 %)
```

O passo 3 garante que **nenhuma marca que já usa modelo hoje perca o modelo** ao subir esta
feature. O passo 1 recusa asset de outra marca — sem isso, um job com roteamento por tema
carimbaria a arte da marca A nos cortes da marca B.

### 3.4 Desenho do texto

Refatorar o bloco de texto de `generate_auto_thumbnail` em duas funções puras, testáveis sem
banco e sem FFmpeg:

- `_resolve_text_zone(w, h, template) -> (x1, y1, x2, y2)` — % → pixels.
- `_draw_text_in_zone(draw, text, zone, style)` — usa o `_fit_text_into_box` que já existe
  (quebra de linha + redução de corpo até caber), depois alinha cada linha por `text_align`
  e o bloco por `text_valign`.

Corpo inicial da fonte passa a ser relativo à **largura da zona**, não à da imagem:
`initial = int(zone_w * 0.22)`, `min = max(12, int(zone_w * 0.06))`. Numa zona de 38 % de 1280 px
(≈ 486 px) isso começa em ~107 px, próximo do "classificação" do exemplo.

**Com modelo:** não cola o logo (passo 3 de hoje) — a arte já é a identidade da marca, e com
modelo opaco o logo era descartado de qualquer forma.

**Sem modelo:** caminho de hoje, byte por byte — faixa de 20 %, texto centrado, contorno ligado.

### 3.5 Fluxo final

```
frame do vídeo
   ├─ sem modelo → logo + faixa 20 % + texto centrado embaixo   (igual a hoje)
   └─ com modelo → composite do modelo + texto na zona do modelo (novo)
```

---

## 4. Checklist

### PR 1 · Modelo de dados dos modelos de capa

- [ ] `apps/brands/models.py`: 10 campos novos em `BrandAsset` (§3.1) + `default_thumb_template_short/long` em `Brand`
- [ ] `apps/auto_cuts/models.py`: `thumb_template_short` e `thumb_template_long` em `AutoCutAnalysis`
- [ ] ~~`MultipleCreatorJob`~~ — descartado, ver §3.2: fan-out multi-marca resolve pelo padrão de cada marca
- [ ] Migrações (`brands` a partir de `0035`, `auto_cuts` a partir de `0033`)
- [ ] `apps/brands/admin.py`: expor os campos novos no `BrandAssetAdmin`
- [ ] Teste: criar dois `BrandAsset` `THUMB_LONG` na mesma marca com rótulos diferentes e conferir que ambos persistem

### PR 2 · Desenho do texto na zona do modelo

- [ ] `thumbnail.py`: `_resolve_template(analysis, brand, is_short)` com a ordem de §3.3
- [ ] `thumbnail.py`: `_resolve_text_zone` e `_draw_text_in_zone` (§3.4)
- [ ] `generate_auto_thumbnail`: bifurcar com modelo / sem modelo; pular o logo quando há modelo
- [ ] Cor/fonte/contorno do modelo com fallback para os da marca
- [ ] `apps/auto_cuts/tests/test_thumbnail_modelo.py` (novo):
  - [ ] sem modelo → pixel no centro da faixa inferior tem a `band_color` (fallback intacto)
  - [ ] com modelo → a faixa inferior **não** é `band_color` e há pixels de texto dentro da zona
  - [ ] com modelo → **nenhum** pixel de texto fora da zona (o bug que estamos corrigindo)
  - [ ] título longo demais reduz o corpo e continua dentro da zona
  - [ ] asset de outra marca é ignorado e cai no padrão da marca do corte
  - [ ] marca com um `THUMB_LONG` e sem padrão configurado continua usando esse modelo (regressão do passo 3)
- [ ] Gerar uma capa de amostra a partir de `docs/cortes_sala_modelo.png` e conferir a olho contra `docs/cortes_sala_exemplo.png`

### PR 3 · API

- [ ] `BrandAssetSerializer`: campos novos; validar faixas e `x+w ≤ 100`, `y+h ≤ 100`
- [ ] `BrandAssetSerializer`: exigir `label` não vazio quando `asset_type` for `THUMB_*` (senão o segundo upload colide no `unique_together`) e aceitar só PNG/JPG
- [ ] `BrandSerializer`: `default_thumb_template_short/long`, validando que o asset é da própria marca e do tipo certo
- [ ] `AutoCutAnalysisSerializer`: `thumb_template_short`, `thumb_template_long`
- [ ] `views/auto_cuts.py` `create` (~242), `upload_ready_cuts` (~342) e `finalizar` (~416): ler, validar (existe + é da marca + tipo bate com o formato) e gravar os dois FKs — mesmo formato do `long_overlay_asset`
- [ ] `tasks_auto_fetch.py:236`: propagar `brand.default_thumb_template_*` para a análise criada
- [ ] `multiple_creator/tasks.py:260`: copiar os FKs do job para a análise filha
- [ ] Testes de API: escolha válida grava; asset de outra marca → 400; tipo trocado (short recebendo `THUMB_LONG`) → 400

### PR 4 · Frontend — cadastro de modelos

- [ ] `IntroOutro.jsx:509`: **remover** o `delete` automático dos `THUMB_*` anteriores
- [ ] Formulário de upload de modelo: rótulo obrigatório + campos da zona (x, y, largura, altura, alinhamento, cor, contorno, fonte) com os padrões de §3.1
- [ ] Prévia: a imagem do modelo com um retângulo posicionado por CSS nas mesmas %, atualizando ao digitar — é o que evita ida e volta para acertar a zona
- [ ] Lista de assets: miniatura, rótulo e zona de cada modelo, com editar e apagar
- [ ] Configuração da marca: seletor do modelo padrão de shorts e de longs
- [ ] Atualizar o texto de ajuda (`IntroOutro.jsx:1315`), que hoje diz "O título é desenhado por cima na faixa inferior"

### PR 5 · Frontend — escolha no job

- [ ] `CortesAutomaticos.jsx`: carregar `THUMB_SHORT` e `THUMB_LONG` da marca ativa (como já se faz com `OVERLAY_LONG` na linha 429)
- [ ] Dois seletores no formulário de criação, com "Nenhum (faixa inferior)" como primeira opção e o padrão da marca pré-selecionado
- [ ] Mesmos seletores no modal de cortes prontos
- [ ] `api.js`: enviar `thumb_template_short`/`thumb_template_long` em `createAutoCut`, `uploadReadyCuts` e `finalizar`
- [ ] Mensagem quando a marca não tem nenhum modelo cadastrado, com link para a página de Marcas

### Fora do escopo (anotado, não feito)

- Hierarquia de texto (subtítulo/bullets como no exemplo) — exige mexer nos prompts e no schema da LLM
- Prévia da capa renderizada pelo backend antes de rodar o job
- Modelo de capa por categoria de tema

---

## 5. Riscos

| Risco | Mitigação |
| --- | --- |
| Marca que já usa modelo perde a capa ao subir a feature | Passo 3 da resolução (§3.3) mantém o comportamento atual; teste de regressão no PR 2 |
| Texto vaza da zona em título muito longo | `_fit_text_into_box` já reduz o corpo; teste dedicado no PR 2 |
| Segundo upload de modelo com rótulo vazio estoura 500 no `unique_together` | Rótulo obrigatório validado no serializer (PR 3) antes de liberar o multi-upload na UI (PR 4) |
| Modelo apagado com job em andamento | FK é `SET_NULL` → a resolução cai para o padrão da marca e, no limite, para o fallback |
| Modelo 16:9 escolhido para short | Validação por formato na API (PR 3) e seletores separados na UI (PR 5) |

---

## 6. Definition of Done

- [ ] `.venv/Scripts/ruff.exe check .` limpo
- [ ] `DJANGO_SETTINGS_MODULE=social_automation.settings_test python manage.py test` verde
- [ ] Uma marca com **dois** modelos de long cadastrados, gerando cortes com cada um e as duas capas conferidas a olho
- [ ] Uma marca **sem** modelo gerando capa idêntica à de antes da feature

---

## 7. Registro de execução

| Data | PR | O que entrou |
| --- | --- | --- |
| — | — | — |
