# -*- coding: utf-8 -*-
"""Interoperabilidad con la Historia Clinica Electronica Interoperable (IHCE).

Cumple la Resolucion 1888 de 2025 del Ministerio de Salud y Proteccion Social,
que adopta el Resumen Digital de Atencion en Salud (RDA) y obliga a todo
prestador a remitir uno por cada atencion a la plataforma nacional, en HL7 FHIR.

Norma aplicable
---------------
- Ley 2015 de 2020, historia clinica electronica interoperable
- Resolucion 866 de 2021, datos clinicos relevantes
- Resolucion 1888 de 2025, adopcion del RDA. Vigencia 15/10/2025, plazo de
  integracion de seis meses

Fuentes tecnicas
----------------
- Guia de implementacion: https://vulcano.ihcecol.gov.co/
- Manual de operaciones de interoperabilidad IHCE v1.4, Ministerio de Salud

Como se usa
-----------
    from ihce import IHCEConfig, encolar, procesar_pendientes

    encolar(db, historia)                     # al cerrar la atencion
    procesar_pendientes(db, IHCEConfig.from_env())   # desde el proceso de envio

Lo que este paquete no puede verificar por si solo: que el Ministerio acepte los
documentos. Eso exige credenciales reales contra el ambiente de pruebas. El
mapeo se construyo contra los perfiles publicados y se valida localmente con las
reglas del manual, pero la prueba de fuego es el sandbox.
"""

from .client import DuplicadoError, IHCEClient, IHCEError
from .config import IHCEConfig
from .mapping import construir_bundle_consulta
from .outbox import (armar_bundle, encolar, procesar_envio, procesar_pendientes,
                     resumen_estado)
from .terminology import GUIA_VERSION
from .validation import ValidationError, validar_bundle, validar_datos_minimos

__all__ = [
    'IHCEConfig', 'IHCEClient', 'IHCEError', 'DuplicadoError',
    'construir_bundle_consulta', 'validar_bundle', 'validar_datos_minimos',
    'ValidationError', 'encolar', 'procesar_envio', 'procesar_pendientes',
    'armar_bundle', 'resumen_estado', 'GUIA_VERSION',
]
