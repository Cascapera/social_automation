"""A transcrição vai ao LLM em bloco único (FEATURE_PROMPTS_SELECAO_CORTES, PR 9).

Antes disto a transcrição passava por `chunk_transcript(chunk_minutes=18, overlap_minutes=3)`
antes de virar prompt — resto do desenho original, em que cada bloco era uma requisição e um
terceiro prompt agregava os resultados. O código virou requisição única e o overlap ficou:
3 minutos repetidos em cada fronteira, **dentro da mesma mensagem**.

Medido na transcrição real de 3h do dump (3.794 segmentos): 222.711 chars com overlap contra
188.704 em bloco único. 15% do input pago para mandar o mesmo trecho duas vezes, com os
mesmos timestamps — e sem nenhuma deduplicação por timestamp no backend, então o mesmo
momento podia ocupar duas vagas do job.

O marcador `--- BLOCO n ---` saiu junto. Ele custava quase nada em tokens, mas com blocos
contíguos ele desenha uma fronteira onde não existe nenhuma: um momento que atravessa 18:00
ficava partido entre dois rótulos.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from apps.auto_cuts.services.grok import _build_chunks_block


class BlocoUnicoTests(SimpleTestCase):
    """Um chunk é o caminho normal: o texto vai limpo."""

    def test_um_unico_chunk_vai_sem_marcador(self):
        bloco = _build_chunks_block([{"text": "[00:00] olá\n[00:05] mundo"}])

        self.assertEqual(bloco, "[00:00] olá\n[00:05] mundo")
        self.assertNotIn("BLOCO", bloco)
        self.assertNotIn("---", bloco)

    def test_um_unico_chunk_em_ingles_tambem_vai_sem_marcador(self):
        bloco = _build_chunks_block([{"text": "[00:00] hello"}], lang="en")

        self.assertEqual(bloco, "[00:00] hello")
        self.assertNotIn("BLOCK", bloco)

    def test_o_texto_chega_intacto(self):
        """Sem reordenar, sem cortar, sem reescrever timestamp."""
        texto = "\n".join(f"[{m:02d}:00] linha {m}" for m in range(30))

        self.assertEqual(_build_chunks_block([{"text": texto}]), texto)

    def test_chunk_vazio_nao_vira_bloco(self):
        self.assertEqual(_build_chunks_block([{"text": "   "}]), "")

    def test_sem_chunk_nenhum_devolve_vazio(self):
        self.assertEqual(_build_chunks_block([]), "")


class VariosChunksTests(SimpleTestCase):
    """Com mais de um, o marcador volta — aí ele separa de verdade."""

    def test_dois_chunks_recebem_marcador_numerado(self):
        bloco = _build_chunks_block([{"text": "um"}, {"text": "dois"}])

        self.assertIn("--- BLOCO 1 ---", bloco)
        self.assertIn("--- BLOCO 2 ---", bloco)

    def test_marcador_respeita_o_idioma(self):
        bloco = _build_chunks_block([{"text": "one"}, {"text": "two"}], lang="en")

        self.assertIn("--- BLOCK 1 ---", bloco)
        self.assertNotIn("BLOCO", bloco)

    def test_chunk_vazio_no_meio_nao_ganha_numero(self):
        """Numerar um bloco que não existe deixaria buraco na contagem."""
        bloco = _build_chunks_block([{"text": "um"}, {"text": ""}, {"text": "tres"}])

        self.assertIn("--- BLOCO 1 ---", bloco)
        self.assertIn("--- BLOCO 2 ---", bloco)
        self.assertNotIn("--- BLOCO 3 ---", bloco)
        self.assertIn("tres", bloco)


class SemDuplicacaoTests(SimpleTestCase):
    """O ponto do PR: nenhum trecho da transcrição aparece duas vezes."""

    def test_o_bloco_tem_cada_timestamp_uma_vez_so(self):
        from apps.auto_cuts.services.transcript import segments_to_transcript_with_timestamps

        # 40 min de segmentos: com blocos de 18 min e overlap de 3, os minutos 15-18 e
        # 33-36 apareciam duas vezes.
        segmentos = [
            {"start": i * 10.0, "end": i * 10.0 + 10, "text": f"fala {i}"}
            for i in range(240)
        ]
        texto = segments_to_transcript_with_timestamps(segmentos)

        bloco = _build_chunks_block([{"text": texto}])

        linhas = bloco.splitlines()
        for i in (95, 96, 97, 100, 200):
            with self.subTest(segmento=i):
                repetidas = [linha for linha in linhas if linha.endswith(f"fala {i}")]
                self.assertEqual(len(repetidas), 1, repetidas)
        self.assertEqual(len(linhas), 240)
        self.assertEqual(len(set(linhas)), 240)
