"""IA predictiva local — Sprint 3, Fase A (RF-301 a RF-304).

Separación de cargas pensada para el reparto Máquina 2 → Máquina 1 (SDD §2.2,
SRS RF-301): el entrenamiento ocurre en la GPU de M1, pero la construcción del
dataset, la API de inferencia y su cliente viven en M2.

División por importación, no solo por carpeta
---------------------------------------------
* `features.py`, `dataset.py` y `client.py` — **no importan `torch`**. Corren en
  M2 con el grupo `base`. Puede importarlos quien solo tenga numpy instalado.
* `model.py`, `xai.py` y `train.py` — sí importan `torch`. Son el grupo `ai`, que
  solo se instala en M1 (CUDA) y en M2 (CPU, para pruebas).

`__init__.py` no importa nada de esto a propósito: `import src.ai` no debe
arrastrar numpy, influxdb-client ni torch al coste de quien solo quiera revisar
el paquete.
"""

__all__ = ["features", "dataset", "model", "xai", "train", "client"]
