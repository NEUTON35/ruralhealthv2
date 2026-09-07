# -*- coding: utf-8 -*-
"""Configuracion de la conexion con el IHCE.

Las credenciales las entrega el Ministerio a traves de Hercules (SISPRO) despues
de registrar al prestador y a su delegado. Son cuatro datos:

- `tenant_id`, `client_id`, `client_secret`: la aplicacion registrada en Azure AD
  contra la que se pide el token OAuth2.
- `subscription_key`: la clave del API Management del ambiente destino, que viaja
  en el header `Ocp-Apim-Subscription-Key`.

La URL base la publica el Ministerio en el momento de entregar las credenciales
y cambia entre el ambiente de pruebas y el de produccion, asi que no se quema en
el codigo.

Ninguno de estos valores se registra en logs. `describe()` existe para que el
operador pueda verificar que estan puestos sin exponerlos.
"""

import os


class IHCEConfig:
    """Credenciales y ambiente del IHCE, leidos del entorno."""

    def __init__(self, tenant_id=None, client_id=None, client_secret=None,
                 subscription_key=None, base_url=None, scope=None,
                 habilitacion=None, sede='00', timeout=30, enabled=None):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.subscription_key = subscription_key
        self.base_url = (base_url or '').rstrip('/')
        self.scope = scope
        self.habilitacion = habilitacion
        self.sede = sede or '00'
        self.timeout = timeout
        self._enabled = enabled

    @classmethod
    def from_env(cls, env=None):
        env = env if env is not None else os.environ
        crudo = env.get('IHCE_ENABLED')
        enabled = None if crudo is None else crudo.strip().lower() in ('1', 'true', 'si', 'sí', 'yes')
        try:
            timeout = int(env.get('IHCE_TIMEOUT', '30'))
        except ValueError:
            timeout = 30
        return cls(
            tenant_id=env.get('IHCE_TENANT_ID'),
            client_id=env.get('IHCE_CLIENT_ID'),
            client_secret=env.get('IHCE_CLIENT_SECRET'),
            subscription_key=env.get('IHCE_SUBSCRIPTION_KEY'),
            base_url=env.get('IHCE_BASE_URL'),
            scope=env.get('IHCE_SCOPE'),
            habilitacion=env.get('IHCE_HABILITACION'),
            sede=env.get('IHCE_SEDE', '00'),
            timeout=timeout,
            enabled=enabled,
        )

    @property
    def token_url(self):
        if not self.tenant_id:
            return None
        return 'https://login.microsoftonline.com/%s/oauth2/v2.0/token' % self.tenant_id

    def faltantes(self):
        """Que credenciales faltan para poder transmitir."""
        requeridos = {
            'IHCE_TENANT_ID': self.tenant_id,
            'IHCE_CLIENT_ID': self.client_id,
            'IHCE_CLIENT_SECRET': self.client_secret,
            'IHCE_SUBSCRIPTION_KEY': self.subscription_key,
            'IHCE_BASE_URL': self.base_url,
            'IHCE_SCOPE': self.scope,
            'IHCE_HABILITACION': self.habilitacion,
        }
        return sorted(nombre for nombre, valor in requeridos.items() if not valor)

    @property
    def is_configured(self):
        return not self.faltantes()

    @property
    def is_enabled(self):
        """Si se debe transmitir.

        `IHCE_ENABLED` permite apagar la transmision sin borrar credenciales, por
        ejemplo durante una ventana de mantenimiento del Ministerio. Cuando no
        esta puesta, manda el hecho de tener credenciales completas.
        """
        if self._enabled is not None:
            return self._enabled and self.is_configured
        return self.is_configured

    def describe(self):
        """Estado legible, sin exponer secretos."""
        return {
            'ambiente': self.base_url or 'sin definir',
            'habilitacion': self.habilitacion or 'sin definir',
            'sede': self.sede,
            'credenciales_completas': self.is_configured,
            'transmision_activa': self.is_enabled,
            'faltantes': self.faltantes(),
        }
