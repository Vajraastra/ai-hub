# deps-audit — registro de dependencias de apps externas

**Por qué existe:** hoy cada app de terceros se instala en su propio venv con
todas sus dependencias, incluido su propio `torch`+CUDA (varios GB cada uno).
Cuando varias apps traen el mismo torch/cuda y otras libs, se duplican decenas
de GB y se pierde portabilidad. Este registro mide ese duplicado para evaluar,
**en el futuro**, un **pool compartido** de dependencias. No cambia nada del
método de instalación actual: solo observa y anota.

## Dos vistas

- **DECLARADAS** — lo que pide cada `requirements.txt`/pyproject (intención).
  Disponible sin instalar nada.
- **RESUELTAS** — lo que `pip freeze` deja realmente en el venv tras instalar
  (versiones exactas + transitivas). Aquí aparecen los torch/cuda reales y los
  verdaderos solapamientos. Es la vista que decide la viabilidad del pool.

## Flujo durante la fase de instalación

```bash
PY=hub-webui/.venv/Scripts/python.exe   # cualquier python sirve para parsear

# 1) matriz de declaradas (ya disponible)
$PY deps-audit/audit.py matrix

# 2) tras instalar CADA app, capturar su venv real:
$PY deps-audit/audit.py snapshot comfyui apps/comfyui/.venv/Scripts/python.exe
$PY deps-audit/audit.py snapshot taggui  apps/taggui/.venv/Scripts/python.exe
# ...una por app...

# 3) regenerar la matriz consolidada (declaradas + resueltas)
$PY deps-audit/audit.py matrix
```

Salida en `MATRIX.md`. Los freezes crudos quedan en `resolved/<app>.txt`.

## Cómo leer la matriz

- **Cruzada** = la usan ≥2 apps → candidata a pool compartido.
- **Conflicto de versión** = misma lib, versión distinta entre apps → obstáculo
  para el pool (habría que unificar versión o el pool no aplica a esa lib).
- **Única** = la usa 1 sola app → se quedaría en el venv de esa app.

El objetivo final del análisis: cuantificar **cuántos GB se recuperan** si las
cruzadas-sin-conflicto (sobre todo torch/cuda/nvidia-*) viven una sola vez.
