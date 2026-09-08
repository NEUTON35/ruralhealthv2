# Credenciales y registros del prestador · RuralHealth Connect

**Para:** representante legal de la IPS, o quien lleve los trámites ante el
Ministerio, la DIAN y la SIC
**De:** Juan David Gómez Aragón — RuralHealth Connect
**Contacto:** juan.gomezaragon@uao.edu.co · +57 305 460 9909
**Fecha del documento:** septiembre de 2026

---

## Qué es esto

Son cinco trámites que **no puede hacer el software**. Todos exigen que los
solicite un prestador habilitado, con su NIT y su código de habilitación.

El sistema ya tiene implementado lo que va en cada uno. Lo que falta son las
credenciales y los registros, y sin ellos hay uno que **impide operar
legalmente**.

Están en orden de urgencia real.

---

## 1 · Credenciales del IHCE — BLOQUEA LA OPERACIÓN

**Qué es.** El Ministerio de Salud exige que cada atención en salud se remita
al repositorio nacional como un Registro Digital de Atención (RDA). Lo obliga
la **Resolución 1888 de 2025**.

**Por qué bloquea.** No es un requisito futuro: es una obligación en curso.
Operar sin remitir el RDA es incumplimiento desde la primera consulta. El
propio sistema se niega a declararse apto para despliegue mientras falten estas
credenciales.

**Dónde se piden.** En **Hércules**, la plataforma del SISPRO del Ministerio de
Salud y Protección Social.

**Qué hay que obtener:**

| Dato | Qué es |
| :--- | :--- |
| `IHCE_TENANT_ID` | Identificador del directorio de la entidad |
| `IHCE_CLIENT_ID` | Identificador de la aplicación cliente |
| `IHCE_CLIENT_SECRET` | Clave de la aplicación cliente |
| `IHCE_SCOPE` | Alcance del token OAuth2 |
| `IHCE_SUBSCRIPTION_KEY` | Clave de suscripción a la pasarela de API |
| `IHCE_BASE_URL` | Dirección del entorno (pruebas y producción son distintas) |
| `IHCE_HABILITACION` | Código de habilitación del prestador en el REPS |
| `IHCE_SEDE` | Código de la sede que presta el servicio |

**Qué ya está hecho.** La generación del Bundle FHIR R4, la autenticación
OAuth2, el envío con reintentos y la cola de pendientes. El sistema está
construido para que **la atención clínica nunca dependa de que el Ministerio
esté disponible**: si el envío falla, el paciente se atiende igual y el RDA
queda en cola.

**Pídalas primero para el entorno de pruebas.** Se puede validar el envío
completo sin tocar producción.

---

## 2 · Catálogos oficiales CIE-10 y CUPS

**Qué es.** Las tablas oficiales de diagnósticos (CIE-10) y de procedimientos
(CUPS) del Ministerio.

**Por qué importa.** El sistema trae un arranque mínimo. **Un catálogo
incompleto rechaza códigos válidos al prescribir**, y el médico se queda sin
poder registrar el diagnóstico correcto. En la práctica eso lleva a que teclee
cualquier código que sí acepte, lo que corrompe el RIPS, el RDA y apaga la
detección de eventos notificables al SIVIGILA.

**Dónde se obtienen.** Portal SISPRO del Ministerio de Salud.

**Formato que espera el sistema.** CSV de dos columnas: `codigo,descripcion`.

**Cómo se cargan.** Una vez descargados:

```
python manage.py load-cie10 cie10_oficial.csv
python manage.py load-cups  cups_oficial.csv
```

---

## 3 · Facturación electrónica ante la DIAN

**Qué es.** Un proveedor tecnológico autorizado y una resolución de numeración.

**Por qué importa.** Hoy el sistema genera y numera los documentos, pero los
marca como **pendientes de radicar**. No finge un envío que no ocurrió, que es
lo correcto, pero significa que el paciente paga y no recibe factura.

**Qué hay que obtener:**

- Contrato con un proveedor tecnológico autorizado por la DIAN
- Resolución de numeración, con su rango y su vigencia

**Ojo con el profesional independiente.** Un médico autónomo factura a su
propio nombre, con su cédula y **su propia resolución**. Mezclar su consecutivo
con el de la clínica invalida ambos: cada resolución autoriza un rango a un
emisor concreto. El sistema ya lo contempla con perfiles de facturación
separados, pero cada emisor necesita su propia resolución.

---

## 4 · MIPRES

**Qué es.** El sistema del Ministerio para prescripción de medicamentos y
servicios **no financiados** con recursos de la UPC.

**Por qué importa.** Sin esto, esos medicamentos no se pueden prescribir por el
canal oficial.

**Qué hay que obtener.** Credenciales del prestador ante MIPRES.

**Qué ya está hecho.** La orden médica guarda el número de MIPRES para
vincularse con el reporte oficial. Falta la integración, que requiere las
credenciales.

---

## 5 · Determinación sobre el RNBD

**Qué es.** El Registro Nacional de Bases de Datos de la Superintendencia de
Industria y Comercio.

**La pregunta.** El **Decreto 090 de 2018** solo obliga a inscribirse a:

- sociedades y entidades sin ánimo de lucro con **activos superiores a 100.000
  UVT**, y
- personas jurídicas de naturaleza pública.

Hay que determinar si el prestador cae en alguno de los dos supuestos.

**Por qué no se puede dejar en blanco.** El artículo 13 de la política de datos
tiene que **declarar la situación real**. Afirmar un registro que no existe es
una declaración falsa ante la SIC. Y omitir el registro estando obligado
también es una infracción.

Esta determinación va junto con la revisión del abogado.

---

## Datos del prestador que hay que completar en el sistema

Sin estos, los documentos legales muestran marcadores del tipo
`[[NIT_OPERADOR]]` y **no son publicables**. Se configuran en Ajustes, y solo
puede hacerlo la superadministración.

- Razón social del operador
- NIT
- Domicilio
- Canal de PQRS (correo y dirección física)
- Área o cargo responsable del tratamiento de datos
- Fundamento de la transferencia internacional (lo decide el abogado)

---

## Resumen de a quién se le pide qué

| Trámite | Dónde | ¿Bloquea? |
| :--- | :--- | :--- |
| Credenciales IHCE | Hércules — SISPRO | **Sí** |
| Catálogos CIE-10 y CUPS | SISPRO | No, pero degrada todo |
| Facturación electrónica | Proveedor DIAN | No |
| MIPRES | Ministerio de Salud | Solo para no-UPC |
| Determinación RNBD | SIC / abogado | No, pero es declaración |
