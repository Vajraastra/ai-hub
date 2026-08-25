"""
Construcción y validación de prompts MiniMax H3.

H3 completo son tres módulos y solo uno se liberó: H3-Context-IR (el
preprocesador que convierte una idea suelta en la instrucción estructurada),
H3-Base (33B, lo que corremos) y H3-Regenerate-2K. Los otros dos siguen siendo
API. Por eso el mismo prompt "funciona en Hailuo y sale roto en ComfyUI": en la
nube lo rescata un preprocesador que no tenemos instalado.

Este paquete repone ese módulo. MiniMax no liberó los pesos del Context-IR pero
sí publicó su contrato de salida en `skills/h3-prompt-writing` — las dos guías
de `guides/` son ese texto, sin editar.

  guides.py    — carga y trocea las guías oficiales por sección
  schema.py    — los campos del prompt + ensamblado determinista
  validator.py — el contrato, verificado sin LLM de por medio
"""
