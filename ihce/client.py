# -*- coding: utf-8 -*-
"""Cliente del API de interoperabilidad del Ministerio de Salud.

Autenticacion: OAuth2 client_credentials contra Azure AD, tal como lo documenta
el Manual de operaciones v1.4:

    POST https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token
    Content-Type: application/x-www-form-urlencoded
    grant_type=client_credentials&client_id=..&client_secret=..&scope=..

Cada peticion al API lleva ademas la clave de suscripcion del ambiente en el
header `Ocp-Apim-Subscription-Key`.

Sobre el manejo de errores. La clasificacion importa porque determina si vale la
pena reintentar:

- 400: el documento esta mal armado. Reintentarlo da 400 otra vez. Es un fallo
  permanente que necesita intervencion.
- 409: el RDA ya fue recibido. No es un error: el resultado deseado ya ocurrio,
  asi que se marca como aceptado y no se reintenta.
- 401/403: credenciales. Reintentar sirve solo si el token expiro, asi que se
  descarta el token y se permite un reintento.
- 5xx y errores de red: el otro lado no esta disponible. Reintentar tiene
  sentido, con espera creciente.

Ningun secreto ni contenido clinico se registra en logs. De la respuesta se
guarda el identificador que devuelve el Ministerio y, si falla, el texto del
OperationOutcome, que describe el campo invalido y no el dato del paciente.
"""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from . import terminology as T

logger = logging.getLogger('ihce')


class IHCEError(Exception):
    """Fallo al comunicarse con el IHCE."""

    def __init__(self, mensaje, codigo=None, permanente=False, detalle=None):
        super().__init__(mensaje)
        self.codigo = codigo
        self.permanente = permanente
        self.detalle = detalle


class DuplicadoError(IHCEError):
    """El Ministerio ya tenia este RDA (409). No es un fallo."""

    def __init__(self, mensaje, detalle=None):
        super().__init__(mensaje, codigo=409, permanente=True, detalle=detalle)


class IHCEClient:
    """Cliente minimo del API. Sin dependencias externas, solo urllib."""

    def __init__(self, config, opener=None, reloj=None):
        self.config = config
        # `opener` y `reloj` se inyectan en las pruebas para no tocar la red.
        self._opener = opener or urllib.request.urlopen
        self._reloj = reloj or time.time
        self._token = None
        self._token_expira = 0.0

    # --- Autenticacion ------------------------------------------------------

    def _token_vigente(self):
        """Token en cache. Se renueva 60 s antes de expirar."""
        if self._token and self._reloj() < (self._token_expira - 60):
            return self._token
        return None

    def obtener_token(self, forzar=False):
        if not forzar:
            vigente = self._token_vigente()
            if vigente:
                return vigente

        faltantes = self.config.faltantes()
        if faltantes:
            raise IHCEError(
                'Faltan credenciales del IHCE: %s' % ', '.join(faltantes),
                permanente=True)

        cuerpo = urllib.parse.urlencode({
            'grant_type': 'client_credentials',
            'client_id': self.config.client_id,
            'client_secret': self.config.client_secret,
            'scope': self.config.scope,
        }).encode('utf-8')

        peticion = urllib.request.Request(
            self.config.token_url, data=cuerpo, method='POST',
            headers={'Content-Type': 'application/x-www-form-urlencoded'})

        try:
            with self._opener(peticion, timeout=self.config.timeout) as resp:
                datos = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            # El cuerpo de un error de Azure AD trae el motivo, pero tambien
            # puede traer el client_id. No se registra.
            raise IHCEError('Azure AD rechazo las credenciales (HTTP %s).' % e.code,
                            codigo=e.code, permanente=e.code in (400, 401)) from None
        except Exception as e:
            raise IHCEError('No se pudo contactar Azure AD: %s'
                            % type(e).__name__) from None

        token = datos.get('access_token')
        if not token:
            raise IHCEError('Azure AD no devolvio access_token.', permanente=True)

        self._token = token
        self._token_expira = self._reloj() + float(datos.get('expires_in', 3600))
        return token

    def olvidar_token(self):
        self._token = None
        self._token_expira = 0.0

    # --- Envio --------------------------------------------------------------

    def _headers(self, token):
        return {
            'Authorization': 'Bearer %s' % token,
            'Ocp-Apim-Subscription-Key': self.config.subscription_key,
            'Content-Type': T.CONTENT_TYPE_FHIR,
            'Accept': T.CONTENT_TYPE_FHIR,
        }

    def enviar_rda(self, bundle, operacion=None, reintento_auth=True):
        """Transmite el Bundle. Devuelve la respuesta ya decodificada.

        Lanza `DuplicadoError` en 409 e `IHCEError` en el resto.
        """
        operacion = operacion or T.OPERACION_RDA_CONSULTA
        url = self.config.base_url + operacion
        token = self.obtener_token()

        cuerpo = json.dumps(bundle, ensure_ascii=False).encode('utf-8')
        peticion = urllib.request.Request(url, data=cuerpo, method='POST',
                                          headers=self._headers(token))

        try:
            with self._opener(peticion, timeout=self.config.timeout) as resp:
                crudo = resp.read().decode('utf-8')
                return json.loads(crudo) if crudo else {}
        except urllib.error.HTTPError as e:
            detalle = _leer_operation_outcome(e)
            if e.code == 409:
                raise DuplicadoError(
                    'El Ministerio ya tenia registrado este RDA.',
                    detalle=detalle) from None
            if e.code in (401, 403):
                # Puede ser un token vencido antes de tiempo. Se descarta y se
                # reintenta una sola vez para no entrar en bucle.
                self.olvidar_token()
                if reintento_auth:
                    return self.enviar_rda(bundle, operacion, reintento_auth=False)
                raise IHCEError('El API rechazo la autenticacion (HTTP %s).' % e.code,
                                codigo=e.code, permanente=True, detalle=detalle) from None
            if e.code == 400:
                raise IHCEError(
                    'El Ministerio rechazo la estructura del documento.',
                    codigo=400, permanente=True, detalle=detalle) from None
            raise IHCEError('El API respondio HTTP %s.' % e.code, codigo=e.code,
                            permanente=e.code < 500, detalle=detalle) from None
        except urllib.error.URLError as e:
            raise IHCEError('No hay conexion con el API del Ministerio: %s'
                            % type(e.reason).__name__ if e.reason else 'sin detalle',
                            permanente=False) from None
        except Exception as e:
            raise IHCEError('Fallo inesperado al transmitir: %s'
                            % type(e).__name__, permanente=False) from None


def _leer_operation_outcome(error_http):
    """Extrae el mensaje del OperationOutcome, si viene.

    El OperationOutcome describe el campo invalido, no el dato clinico, asi que
    puede guardarse. Se recorta para que un error largo no llene la tabla.
    """
    try:
        crudo = error_http.read().decode('utf-8')
    except Exception:
        return None
    if not crudo:
        return None
    try:
        datos = json.loads(crudo)
    except ValueError:
        return crudo[:1000]
    if datos.get('resourceType') == 'OperationOutcome':
        partes = []
        for issue in datos.get('issue', []):
            texto = (issue.get('diagnostics')
                     or (issue.get('details') or {}).get('text')
                     or issue.get('code', ''))
            ubicacion = ', '.join(issue.get('expression') or issue.get('location') or [])
            partes.append(('%s [%s]' % (texto, ubicacion)) if ubicacion else texto)
        return '; '.join(p for p in partes if p)[:1000]
    return crudo[:1000]
