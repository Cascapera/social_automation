"""Fluxo de publicação, fatiado por etapa (refactor.md R-09 a R-12 / D-01).

`_run_post_to_platforms` tinha 1.216 linhas e é a maior função do projeto. O plano a corta
em quatro: preflight, publicação nativa no YouTube, ramo Upload-Post e finalização. Este
pacote recebe as fatias, uma por PR.
"""
