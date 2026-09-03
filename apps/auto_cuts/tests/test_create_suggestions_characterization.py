"""Characterization de `_create_suggestions` (FEATURE_PROMPTS_SELECAO_CORTES, F-00).

`_create_suggestions` decide **o que vira corte**: aplica o teto de duração por modo,
ordena por nota, corta na quantidade do job e grava as `AutoCutSuggestion`. São 230 linhas
sem nenhum teste até aqui — `test_analyze_task_characterization.py` cobre de propósito só
as fronteiras da task (desistência silenciosa, `status="error"`, reagendamento, delegação)
e diz isso no próprio docstring.

Este arquivo trava o comportamento **atual**, antes de qualquer mudança do projeto. Ele não
afirma que o comportamento de hoje é o desejado — afirma que é este. Quando uma regra muda
de propósito, a asserção correspondente muda junto, e é essa mudança que aparece na revisão.

Mudanças já registradas por aqui:

  · teto do long 15 min → 40 min · `duration_minutes` do LLM → calculado dos timestamps ·
    mínimo de 8 min passando a valer também no educacional (PR 1, #70)
  · corte na quantidade passou a acontecer depois dos filtros: o job entregava zero short
    tendo três candidatos válidos na fila (PR 5)
  · teto do short educacional 180s → 150s, e teto duro de 170s valendo para todo modo,
    incluindo `prompt_version` desconhecido (PR 7)

É o mesmo mecanismo do hash de prompt em `test_grok_prompts_integridade.py`, aplicado a
comportamento em vez de texto: quem mudar tem que passar por aqui e dizer por quê.

Nada aqui precisa de vídeo, FFmpeg, Whisper ou rede: `_create_suggestions` recebe o dict que
o LLM já devolveu e grava no banco. A extração acontece depois, em
`_extract_cuts_for_suggestions`, e está fora deste arquivo.

Na maior parte dos casos a análise é criada **sem brand** de propósito: sem brand não há
factory, e `_filter_factory_routable_items` deixa tudo passar, isolando sob teste só a regra
de duração, ordenação e limite. A exceção é `RoteamentoPorFactoryTests`, que monta factory de
verdade — porque a **ordem** entre o corte de quantidade e o filtro de roteamento é
exatamente o que o PR 5 muda, e sem caracterizá-la agora não há como provar depois que a
mudança foi a pretendida.
"""

from __future__ import annotations

from django.test import TestCase

from apps.auto_cuts.models import AutoCutAnalysis, AutoCutSuggestion
from apps.auto_cuts.services.analysis_flow import _create_suggestions
from apps.brands.models import Brand, BrandCategory, Factory
from apps.jobs.services.ffmpeg import tc_to_seconds
from apps.mediahub.models import SourceVideo


def short_viral(start: str, end: str, score: int = 80, **extra) -> dict:
    """Item de short no formato dos prompts virais (`start_timestamp`/`end_timestamp`)."""
    item = {
        "clip_number": 1,
        "start_timestamp": start,
        "end_timestamp": end,
        "virality_score": score,
        "suggested_title": "Título forte 🎯",
        "hook_sentence": "frase de impacto",
        "main_topic": "assunto",
        "theme_category": "COMEDY_HUMOR",
    }
    item.update(extra)
    return item


def short_educacional(start: str, end: str, score: int = 8, **extra) -> dict:
    """Item de short no formato do prompt educacional (`start`/`end`, `title`, `rank`)."""
    item = {
        "rank": 1,
        "start": start,
        "end": end,
        "virality_score": score,
        "title": "Título informativo 📚",
        "hook": "pergunta de abertura",
        "reason": "valor didático",
        "theme_category": "BUSINESS_MONEY",
    }
    item.update(extra)
    return item


def long_cut(start: str, end: str, score: int = 80, **extra) -> dict:
    item = {
        "start_timestamp": start,
        "end_timestamp": end,
        "virality_score": score,
        "title_suggestion": "Título do longo 🎯",
        "reason": "por que viraliza",
        "theme_category": "BUSINESS_MONEY",
    }
    item.update(extra)
    return item


class CriarSugestoesMixin:
    """Cria a análise e chama `_create_suggestions` com a resposta já pronta do LLM."""

    def build_analysis(self, **campos) -> AutoCutAnalysis:
        return AutoCutAnalysis.objects.create(status="analyzing", **campos)

    def criar(
        self,
        *,
        pv: str = "viral",
        candidate_shorts: list[dict] | None = None,
        ranked_shorts: list[dict] | None = None,
        long_cuts: list[dict] | None = None,
        analysis: AutoCutAnalysis | None = None,
    ) -> AutoCutAnalysis:
        analysis = analysis or self.build_analysis()
        final = {
            "candidate_shorts": candidate_shorts or [],
            "ranked_shorts": ranked_shorts or [],
            "final_long_cuts": long_cuts or [],
        }
        _create_suggestions(analysis, final, pv)
        return analysis

    def shorts(self, analysis: AutoCutAnalysis) -> list[AutoCutSuggestion]:
        return list(
            AutoCutSuggestion.objects.filter(analysis=analysis, cut_type="short").order_by("rank", "id")
        )

    def longs(self, analysis: AutoCutAnalysis) -> list[AutoCutSuggestion]:
        return list(AutoCutSuggestion.objects.filter(analysis=analysis, cut_type="long").order_by("id"))


class DuracaoDeShortViralTests(CriarSugestoesMixin, TestCase):
    """Modo viral: faixa 30–60s, teto por truncamento, mínimo por descarte."""

    def test_short_acima_do_teto_e_truncado_em_60s(self):
        analysis = self.criar(candidate_shorts=[short_viral("10:00", "11:30")])

        (sug,) = self.shorts(analysis)
        self.assertEqual(sug.duration_seconds, 60)
        self.assertEqual(tc_to_seconds(sug.end_tc) - tc_to_seconds(sug.start_tc), 60)

    def test_short_abaixo_do_minimo_e_descartado(self):
        analysis = self.criar(candidate_shorts=[short_viral("10:00", "10:20", score=99)])

        self.assertEqual(self.shorts(analysis), [])

    def test_short_dentro_da_faixa_passa_intacto(self):
        analysis = self.criar(candidate_shorts=[short_viral("10:00", "10:45")])

        (sug,) = self.shorts(analysis)
        self.assertEqual(sug.duration_seconds, 45)
        self.assertEqual(sug.end_tc, "10:45")

    def test_short_com_fim_antes_do_inicio_e_descartado(self):
        analysis = self.criar(candidate_shorts=[short_viral("10:00", "09:00")])

        self.assertEqual(self.shorts(analysis), [])

    def test_short_com_fim_igual_ao_inicio_e_descartado(self):
        analysis = self.criar(candidate_shorts=[short_viral("10:00", "10:00")])

        self.assertEqual(self.shorts(analysis), [])


class DuracaoDeShortViralLongTests(CriarSugestoesMixin, TestCase):
    """Modo viral_long: faixa 80–160s, com exceção para nota acima de 95."""

    def test_short_acima_do_teto_e_truncado_em_160s(self):
        analysis = self.criar(pv="viral_long", candidate_shorts=[short_viral("10:00", "13:20")])

        (sug,) = self.shorts(analysis)
        self.assertEqual(sug.duration_seconds, 160)

    def test_short_abaixo_de_80s_com_nota_normal_e_descartado(self):
        analysis = self.criar(
            pv="viral_long", candidate_shorts=[short_viral("10:00", "11:10", score=50)]
        )

        self.assertEqual(self.shorts(analysis), [])

    def test_short_abaixo_de_80s_com_nota_acima_de_95_e_mantido(self):
        analysis = self.criar(
            pv="viral_long", candidate_shorts=[short_viral("10:00", "11:10", score=96)]
        )

        (sug,) = self.shorts(analysis)
        self.assertEqual(sug.duration_seconds, 70)

    def test_short_abaixo_de_30s_e_descartado_mesmo_com_nota_maxima(self):
        """O mínimo absoluto de 30s vale mesmo para a exceção de nota alta."""
        analysis = self.criar(
            pv="viral_long", candidate_shorts=[short_viral("10:00", "10:20", score=100)]
        )

        self.assertEqual(self.shorts(analysis), [])


class DuracaoDeShortEducacionalTests(CriarSugestoesMixin, TestCase):
    """Modo educacional: só teto, sem mínimo."""

    def test_short_educacional_acima_do_teto_e_truncado_em_150s(self):
        """O teto era 180s, exatamente o limite do Shorts do YouTube.

        Um corte de 180,0s vira 180,0x depois do re-encode a 30fps e sai da classificação
        de Short — observado em produção. 150s dão 30s de folga.
        """
        analysis = self.criar(
            pv="educational", ranked_shorts=[short_educacional("10:00", "13:20")]
        )

        (sug,) = self.shorts(analysis)
        self.assertEqual(sug.duration_seconds, 150)

    def test_short_educacional_de_170s_tambem_e_truncado(self):
        """Entre o teto antigo e o novo: antes passava inteiro, agora não."""
        analysis = self.criar(
            pv="educational", ranked_shorts=[short_educacional("10:00", "12:50")]
        )

        (sug,) = self.shorts(analysis)
        self.assertEqual(sug.duration_seconds, 150)

    def test_short_educacional_curto_passa_intacto(self):
        """Não há mínimo no modo educacional: 40s passa, onde o viral descartaria."""
        analysis = self.criar(
            pv="educational", ranked_shorts=[short_educacional("10:00", "10:40")]
        )

        (sug,) = self.shorts(analysis)
        self.assertEqual(sug.duration_seconds, 40)


class TetoDuroDeShortTests(CriarSugestoesMixin, TestCase):
    """A rede para o que não tem faixa própria.

    O clamp por modo mora dentro de `if is_viral_prompt / elif is_educational_prompt`. Um
    `prompt_version` fora dessas duas listas — um modo novo cujo autor esqueceu de incluir
    na tupla — caía no `else` implícito e era gravado com a duração crua do LLM: sem teto,
    sem mínimo e sem erro nenhum. O teto duro é aplicado depois dos ramos, sobre todo short.
    """

    def test_prompt_version_desconhecido_bate_no_teto_duro(self):
        analysis = self.criar(pv="modo_que_nao_existe", candidate_shorts=[short_viral("10:00", "16:40")])

        (sug,) = self.shorts(analysis)
        self.assertEqual(sug.duration_seconds, 170)
        self.assertEqual(tc_to_seconds(sug.end_tc) - tc_to_seconds(sug.start_tc), 170)

    def test_nenhum_modo_configurado_produz_short_acima_do_teto_duro(self):
        """Percorre `prompt_version.choices` — modo novo nasce coberto por este teste."""
        modos = [codigo for codigo, _ in AutoCutAnalysis._meta.get_field("prompt_version").choices]
        self.assertEqual(len(modos), 7, modos)

        for pv in modos + ["modo_que_nao_existe", ""]:
            with self.subTest(prompt_version=pv):
                analysis = self.build_analysis()
                self.criar(
                    analysis=analysis,
                    pv=pv,
                    candidate_shorts=[short_viral("10:00", "20:00")],
                    ranked_shorts=[short_educacional("10:00", "20:00")],
                )
                for sug in self.shorts(analysis):
                    duracao = tc_to_seconds(sug.end_tc) - tc_to_seconds(sug.start_tc)
                    self.assertLessEqual(duracao, 170, f"{pv}: {duracao}s")
                    self.assertLess(duracao, 180, f"{pv}: encostou no limite do Shorts")


class DuracaoDeCorteLongoTests(CriarSugestoesMixin, TestCase):
    """Cortes longos: hoje a regra vale só para os modos virais."""

    def test_long_acima_do_teto_e_truncado_em_40_min(self):
        analysis = self.criar(long_cuts=[long_cut("00:00", "45:00")])

        (sug,) = self.longs(analysis)
        self.assertEqual(sug.duration_minutes, 40.0)
        self.assertEqual(tc_to_seconds(sug.end_tc) - tc_to_seconds(sug.start_tc), 40 * 60)

    def test_long_de_20_min_passa_intacto_onde_antes_era_truncado(self):
        """A faixa antiga parava em 15 min: este corte perdia 5 min no meio da frase."""
        analysis = self.criar(long_cuts=[long_cut("00:00", "20:00")])

        (sug,) = self.longs(analysis)
        self.assertEqual(sug.duration_minutes, 20.0)

    def test_long_abaixo_de_8_min_e_descartado(self):
        analysis = self.criar(long_cuts=[long_cut("00:00", "06:00")])

        self.assertEqual(self.longs(analysis), [])

    def test_long_dentro_da_faixa_tem_duracao_calculada_dos_timestamps(self):
        analysis = self.criar(long_cuts=[long_cut("00:00", "12:30")])

        (sug,) = self.longs(analysis)
        self.assertEqual(sug.duration_minutes, 12.5)

    def test_long_com_fim_antes_do_inicio_e_descartado(self):
        analysis = self.criar(long_cuts=[long_cut("20:00", "10:00")])

        self.assertEqual(self.longs(analysis), [])

    def test_long_educacional_ignora_o_duration_min_do_llm(self):
        """RN-02: manda o timecode, não o número que o LLM escreveu.

        Antes do PR 1 o modo educacional não passava pelo clamp e gravava `duration_min`
        como veio — 90, enquanto os timestamps diziam 22 min. Quem extrai o vídeo usa o
        timecode, então o campo mentia para a interface e para qualquer relatório.
        """
        analysis = self.criar(
            pv="educational",
            long_cuts=[long_cut("00:00", "22:00", duration_min=90)],
        )

        (sug,) = self.longs(analysis)
        self.assertEqual(sug.duration_minutes, 22.0)
        self.assertEqual(tc_to_seconds(sug.end_tc) - tc_to_seconds(sug.start_tc), 22 * 60)

    def test_long_educacional_abaixo_do_minimo_e_descartado(self):
        """O mínimo de 8 min passou a valer também fora dos modos virais."""
        analysis = self.criar(
            pv="educational",
            long_cuts=[long_cut("00:00", "02:00", duration_min=2)],
        )

        self.assertEqual(self.longs(analysis), [])

    def test_long_educacional_acima_do_teto_e_truncado_em_40_min(self):
        analysis = self.criar(
            pv="educational",
            long_cuts=[long_cut("00:00", "50:00", duration_min=50)],
        )

        (sug,) = self.longs(analysis)
        self.assertEqual(sug.duration_minutes, 40.0)


class OrdenacaoELimiteTests(CriarSugestoesMixin, TestCase):
    """Ordenação por nota, rank sequencial e o teto de entrega."""

    def test_shorts_saem_ordenados_por_nota_com_rank_sequencial(self):
        analysis = self.criar(
            candidate_shorts=[
                short_viral("10:00", "10:40", score=50),
                short_viral("20:00", "20:40", score=90),
                short_viral("30:00", "30:40", score=70),
            ]
        )

        sugs = self.shorts(analysis)
        self.assertEqual([s.virality_score for s in sugs], [90, 70, 50])
        self.assertEqual([s.rank for s in sugs], [1, 2, 3])

    def test_shorts_target_limita_a_entrega(self):
        analysis = self.build_analysis(shorts_target=2)
        self.criar(
            analysis=analysis,
            candidate_shorts=[
                short_viral(f"{m:02d}:00", f"{m:02d}:40", score=50 + m) for m in range(1, 6)
            ],
        )

        self.assertEqual(len(self.shorts(analysis)), 2)

    def test_shorts_target_acima_de_10_e_silenciosamente_cortado_em_10(self):
        """O campo aceita até 30; a entrega para em 10, sem aviso (questão Q-02)."""
        analysis = self.build_analysis(shorts_target=20)
        self.criar(
            analysis=analysis,
            candidate_shorts=[
                short_viral(f"{m:02d}:00", f"{m:02d}:40", score=50) for m in range(1, 16)
            ],
        )

        self.assertEqual(len(self.shorts(analysis)), 10)

    def test_longs_target_limita_a_entrega(self):
        analysis = self.build_analysis(longs_target=1)
        self.criar(
            analysis=analysis,
            long_cuts=[long_cut("00:00", "10:00", score=60), long_cut("20:00", "30:00", score=90)],
        )

        sugs = self.longs(analysis)
        self.assertEqual(len(sugs), 1)
        self.assertEqual(sugs[0].virality_score, 90)

    def test_viral_long_ordena_por_nota_e_duracao_combinadas(self):
        """No viral_long a ordem é 50% nota + 50% duração normalizada em 160s.

        O clipe de nota 80 e 160s (composite 90) passa na frente do de nota 100 e 90s
        (composite 78,1) — ordenar só por nota daria o inverso.
        """
        analysis = self.criar(
            pv="viral_long",
            candidate_shorts=[
                short_viral("10:00", "11:30", score=100),
                short_viral("20:00", "22:40", score=80),
            ],
        )

        sugs = self.shorts(analysis)
        self.assertEqual([s.virality_score for s in sugs], [80, 100])

    def test_modo_viral_prefere_candidate_shorts(self):
        analysis = self.criar(
            candidate_shorts=[short_viral("10:00", "10:40", score=10)],
            ranked_shorts=[short_viral("20:00", "20:40", score=99)],
        )

        (sug,) = self.shorts(analysis)
        self.assertEqual(sug.virality_score, 10)

    def test_modo_educacional_prefere_ranked_shorts(self):
        analysis = self.criar(
            pv="educational",
            candidate_shorts=[short_educacional("10:00", "10:40", score=1)],
            ranked_shorts=[short_educacional("20:00", "20:40", score=9)],
        )

        (sug,) = self.shorts(analysis)
        self.assertEqual(sug.virality_score, 9)

    def test_sugestoes_anteriores_sao_apagadas_antes_de_recriar(self):
        analysis = self.build_analysis()
        self.criar(analysis=analysis, candidate_shorts=[short_viral("10:00", "10:40")])
        self.criar(analysis=analysis, candidate_shorts=[short_viral("20:00", "20:40")])

        sugs = self.shorts(analysis)
        self.assertEqual(len(sugs), 1)
        self.assertEqual(sugs[0].start_tc, "20:00")


class RoteamentoPorFactoryTests(CriarSugestoesMixin, TestCase):
    """Ordem entre o corte de quantidade e o filtro de roteamento.

    Em contexto de factory, item cuja `theme_category` não tem brand mapeada é descartado.
    Hoje esse descarte acontece **depois** do corte na quantidade alvo — e é isso que faz um
    job entregar menos do que pediu mesmo tendo candidato válido de sobra na fila.
    """

    def build_factory_analysis(self, **campos) -> AutoCutAnalysis:
        factory = Factory.objects.create(name="Factory F00")
        BrandCategory.objects.create(
            factory=factory, code="COMEDY_HUMOR", label="Comédia", is_active=True
        )
        brand = Brand.objects.create(
            name="Brand F00", slug="brand-f00", factory=factory, theme_category="COMEDY_HUMOR"
        )
        return AutoCutAnalysis.objects.create(status="analyzing", brand=brand, **campos)

    def test_item_sem_brand_mapeada_para_a_categoria_e_descartado(self):
        analysis = self.build_factory_analysis()
        self.criar(
            analysis=analysis,
            candidate_shorts=[
                short_viral("10:00", "10:40", score=90, theme_category="BUSINESS_MONEY"),
                short_viral("20:00", "20:40", score=50, theme_category="COMEDY_HUMOR"),
            ],
        )

        sugs = self.shorts(analysis)
        self.assertEqual(len(sugs), 1)
        self.assertEqual(sugs[0].theme_category, "COMEDY_HUMOR")

    def test_item_sem_categoria_nenhuma_e_descartado(self):
        analysis = self.build_factory_analysis()
        self.criar(
            analysis=analysis,
            candidate_shorts=[short_viral("10:00", "10:40", theme_category="")],
        )

        self.assertEqual(self.shorts(analysis), [])

    def test_candidato_valido_da_fila_ocupa_a_vaga_do_descartado(self):
        """CA-11: o corte na quantidade acontece DEPOIS dos filtros.

        Os dois primeiros colocados não têm brand mapeada. Até o PR 5 eles ocupavam as duas
        vagas do job e eram descartados em seguida, e o job entregava **zero** short mesmo
        com três candidatos válidos logo atrás na fila. Agora os válidos assumem as vagas.
        """
        analysis = self.build_factory_analysis(shorts_target=2)
        self.criar(
            analysis=analysis,
            candidate_shorts=[
                short_viral("10:00", "10:40", score=90, theme_category="BUSINESS_MONEY"),
                short_viral("11:00", "11:40", score=85, theme_category="BUSINESS_MONEY"),
                short_viral("12:00", "12:40", score=50, theme_category="COMEDY_HUMOR"),
                short_viral("13:00", "13:40", score=49, theme_category="COMEDY_HUMOR"),
                short_viral("14:00", "14:40", score=48, theme_category="COMEDY_HUMOR"),
            ],
        )

        sugs = self.shorts(analysis)
        self.assertEqual([s.start_tc for s in sugs], ["12:00", "13:00"])
        self.assertEqual([s.virality_score for s in sugs], [50, 49])


class NormalizacaoDeCamposTests(CriarSugestoesMixin, TestCase):
    """Como a nota e os textos chegam ao banco."""

    def test_nota_com_simbolo_de_percentual_vira_inteiro(self):
        analysis = self.criar(candidate_shorts=[short_viral("10:00", "10:40", score="96%")])

        (sug,) = self.shorts(analysis)
        self.assertEqual(sug.virality_score, 96)

    def test_nota_acima_de_100_e_limitada(self):
        analysis = self.criar(candidate_shorts=[short_viral("10:00", "10:40", score=150)])

        (sug,) = self.shorts(analysis)
        self.assertEqual(sug.virality_score, 100)

    def test_nota_invalida_vira_nulo_sem_derrubar_o_corte(self):
        analysis = self.criar(candidate_shorts=[short_viral("10:00", "10:40", score="alta")])

        (sug,) = self.shorts(analysis)
        self.assertIsNone(sug.virality_score)

    def test_backend_nunca_reescalona_a_nota_que_o_llm_devolveu(self):
        """A escala é definida no prompt, não no backend — que só normaliza e limita.

        Até o PR 3 o modo educacional pedia 1–10 enquanto todos os outros pediam 0–100, e
        como aqui não há conversão, um corte educacional excelente ficava gravado como 9 ao
        lado de um viral 96. Hoje os dois prompts pedem 0–100; este teste existe para que a
        ausência de conversão continue sendo uma escolha visível, e não uma surpresa para
        quem introduzir uma escala nova num prompt.
        """
        analysis = self.criar(pv="educational", ranked_shorts=[short_educacional("10:00", "12:00", score=9)])

        (sug,) = self.shorts(analysis)
        self.assertEqual(sug.virality_score, 9)

    def test_raw_data_preserva_o_item_do_llm(self):
        item = short_viral("10:00", "10:40", author_cue="presta atenção nessa parte")
        analysis = self.criar(candidate_shorts=[item])

        (sug,) = self.shorts(analysis)
        self.assertEqual(sug.raw_data["author_cue"], "presta atenção nessa parte")

    def test_convidados_e_anexado_ao_titulo(self):
        analysis = self.build_analysis(convidados="Renato Albani")
        self.criar(analysis=analysis, candidate_shorts=[short_viral("10:00", "10:40")])

        (sug,) = self.shorts(analysis)
        self.assertEqual(sug.title, "Título forte 🎯 + Renato Albani")

    def test_source_asset_id_usa_a_source_quando_houver(self):
        """A source tem precedência sobre a URL do YouTube."""
        brand = Brand.objects.create(name="Brand src", slug="brand-src")
        source = SourceVideo.objects.create(brand=brand, title="Episódio", file="sources/ep.mp4")
        analysis = self.build_analysis(source=source, youtube_url="https://youtu.be/abc123")
        self.criar(analysis=analysis, candidate_shorts=[short_viral("10:00", "10:40")])

        (sug,) = self.shorts(analysis)
        self.assertEqual(sug.source_asset_id, str(source.id))

    def test_source_asset_id_usa_a_url_do_youtube_quando_houver(self):
        analysis = self.build_analysis(youtube_url="https://youtu.be/abc123")
        self.criar(analysis=analysis, candidate_shorts=[short_viral("10:00", "10:40")])

        (sug,) = self.shorts(analysis)
        self.assertEqual(sug.source_asset_id, "https://youtu.be/abc123")

    def test_source_asset_id_cai_para_o_id_da_analise_sem_fonte(self):
        analysis = self.criar(candidate_shorts=[short_viral("10:00", "10:40")])

        (sug,) = self.shorts(analysis)
        self.assertEqual(sug.source_asset_id, f"analysis:{analysis.id}")

    def test_formato_devolvido_e_vertical_para_short_e_horizontal_para_long(self):
        analysis = self.build_analysis()
        final = {
            "candidate_shorts": [short_viral("10:00", "10:40")],
            "ranked_shorts": [],
            "final_long_cuts": [long_cut("00:00", "10:00")],
        }

        criadas = _create_suggestions(analysis, final, "viral")

        self.assertEqual([fmt for _, fmt in criadas], ["vertical", "horizontal"])
