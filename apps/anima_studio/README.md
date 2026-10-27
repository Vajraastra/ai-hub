# Taller Anima · PoC

Taller local de arte para varios juegos. Cada proyecto guarda sus formatos, categorías y reglas
de organización. La biblioteca comparte estilos y piezas únicamente cuando se elige ese ámbito.

## Abrir

Desde la raíz del Hub, abrir `run_anima_studio.bat`. Prepara su entorno con uv y abre
`http://127.0.0.1:9754`. Iniciar ComfyUI desde el Hub para generar; cerrar el taller con Ctrl+C.
También está integrado como herramienta interna en `/anima-studio` del Hub.

Lee `paths.models` de `hub/hub_config.json`. Para una copia de desarrollo puede definirse
`AI_HUB_CONFIG` con la ruta al archivo de configuración de la instalación. No copia ni cambia
pesos: muestra modelos y LoRAs/LoKr compatibles con Anima por sus encabezados safetensors.
El encoder Qwen3-0.6B y el VAE Qwen-Image se seleccionan de esa misma biblioteca.

## Recorrido

1. Crear un proyecto y ajustar categorías/formatos. Dungeon Tycoon es un perfil inicial editable.
2. En **Estilos**, elegir modelo, LoRAs, pesos, prompt de acabado y sampling. La escena de prueba
   es independiente del prompt de estilo. Comparar muestras con la misma seed y guardar el estilo.
3. En **Producción**, seleccionar un estilo y combinar personaje, personalidad, outfit, pose,
   expresión y escenario. Guardar la combinación como receta para recuperarla después.
4. Para expresiones, elegir un maestro del proyecto o cargar un PNG, pintar la región facial y
   ajustar la intensidad. El parche se compone sobre el original; conserva el alfa y todos los
   píxeles exteriores a la máscara final. Cada expresión debe partir del maestro de referencia.
5. Aceptar, rechazar o restaurar imágenes desde la galería. Los aceptados se organizan por
   proyecto/categoría/personaje/outfit; los rechazados permanecen recuperables hasta su limpieza
   explícita. La limpieza protege maestros y muestras referenciadas por versiones guardadas.

Las seeds se conservan como texto para no perder precisión de 64 bits. El historial guarda
configuración efectiva, workflow, piezas, revisiones y procedencia. Cambiar una pieza o estilo
no modifica las generaciones anteriores. Importar/exportar perfiles conserva las revisiones
fijadas por sus recetas; los archivos de modelos e imágenes no se incluyen en ese JSON.

## Datos locales

`data/` está excluido de Git. Contiene biblioteca, revisiones, historial, aceptados y rechazados.
Para respaldar o trasladar la biblioteca completa, conservar este directorio. Los metadatos JSON
acompañan al PNG; las expresiones incluyen maestro, parche y máscaras.

`ANIMA_STUDIO_DATA` permite elegir otro directorio de datos. El PoC organiza los recursos dentro
de esa biblioteca; la descarga PNG entrega el archivo con su nombre contextual. No escribe
automáticamente dentro del repositorio de un juego.

## Alcance

- Referencias IP-Adapter y control visual de pose 3D pendientes. Las piezas de pose son prompts.
- Txt2img produce RGB: el requisito alfa de un formato no elimina el fondo. Las expresiones
  conservan el alfa que ya tiene su maestro. El recorte de fondos automático queda pendiente.
- La comprobación exterior a la máscara es técnica; rostro, transición y acabado requieren
  revisión humana. La seed por sí sola no garantiza identidad ni igualdad entre configuraciones.
- Calibración por pruebas individuales y comparación de galería; barridos automáticos pendientes.
- Coordinación con ComfyUI dentro del único proceso del Hub. Otros clientes y procesos están
  fuera de esa coordinación; no ejecutar simultáneamente el Hub completo y el servidor PoC.
- El plazo de retención es un filtro configurable para limpieza manual; no hay borrado por reloj.

## Verificación sin GPU

Desde la raíz y con el Python del entorno del Hub:

```powershell
hub-webui/.venv/Scripts/python.exe apps/anima_studio/tests/check_library.py
hub-webui/.venv/Scripts/python.exe apps/anima_studio/tests/check_generation.py
hub-webui/.venv/Scripts/python.exe apps/anima_studio/tests/check_api.py
hub-webui/.venv/Scripts/python.exe hub-webui/tests/test_comfy_jobs.py -v
```
