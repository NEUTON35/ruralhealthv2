# Todo lo que falta · RuralHealth Connect

Lista única, para ir tachando. Actualizada al **8 de septiembre de 2026**.

Cada punto dice **quién** lo hace y **qué desbloquea**, porque el orden no es
por dificultad sino por lo que impide.

Marca así: `- [x]` cuando esté hecho.

---

## Bloquea el despliegue

Mientras esto siga abierto, `python manage.py preflight` responde
**NO APTO PARA DESPLIEGUE**, y con razón.

- [ ] **Credenciales del IHCE** en Hércules (SISPRO)
    - *Quién:* representante legal de la IPS
    - *Desbloquea:* poder operar legalmente. La Resolución 1888 de 2025 obliga a remitir un RDA por cada atención.
    - *Detalle:* `entregables/3-tramites-del-prestador/`
    - *Ocho valores:* `IHCE_TENANT_ID`, `IHCE_CLIENT_ID`, `IHCE_CLIENT_SECRET`, `IHCE_SCOPE`, `IHCE_SUBSCRIPTION_KEY`, `IHCE_BASE_URL`, `IHCE_HABILITACION`, `IHCE_SEDE`

---

## Necesita a un profesional que no eres tú

- [ ] **Revisión de la base de conocimiento clínico**
    - *Quién:* químico farmacéutico
    - *Desbloquea:* que las alertas de alergia e interacción sean confiables.
    - *Detalle:* `entregables/2-quimico-farmaceutico/`
    - *Estado:* 145 principios activos, 40 interacciones, 21 contraindicaciones en embarazo. Marcado en el código como «revisión inicial de atención primaria».

- [ ] **Revisión de los cinco textos legales**
    - *Quién:* abogado con tarjeta profesional
    - *Desbloquea:* publicar los documentos.
    - *Detalle:* `entregables/1-abogado/`
    - *Seis decisiones concretas:* cláusula de responsabilidad, fundamento de la transferencia internacional, contrato de transmisión con cada IPS, régimen del profesional independiente, acreditación de patria potestad, y consentimiento informado asistencial.

- [ ] **Determinación sobre el RNBD** (Decreto 090 de 2018)
    - *Quién:* abogado
    - *Desbloquea:* el artículo 13 de la política. Afirmar un registro inexistente es declaración falsa ante la SIC.

---

## Trámites del prestador

- [ ] **Cargar los catálogos oficiales CIE-10 y CUPS**
    - *Quién:* tú, una vez descargados del SISPRO
    - *Desbloquea:* que el médico pueda registrar el diagnóstico correcto. Un catálogo incompleto rechaza códigos válidos, y eso corrompe el RIPS, el RDA y apaga la detección al SIVIGILA.
    - *Comando:* `python manage.py load-cie10 archivo.csv`

- [ ] **Proveedor de facturación electrónica y resolución de numeración DIAN**
    - *Quién:* representante legal
    - *Desbloquea:* emitir factura. Hoy los documentos se generan y numeran pero quedan marcados como pendientes de radicar.

- [ ] **Credenciales de MIPRES**
    - *Quién:* representante legal
    - *Desbloquea:* prescribir medicamentos no financiados con UPC.

- [ ] **Completar los datos del prestador en Ajustes**
    - *Quién:* tú, con los datos que dé el representante legal
    - *Desbloquea:* que los documentos legales dejen de mostrar marcadores.
    - *Campos:* razón social, NIT, domicilio, canal de PQRS, área responsable, fundamento de la transferencia internacional.

---

## Configuración del despliegue

- [ ] **`RURALHEALTH_ALLOWED_HOSTS`** con el dominio real
    - *Por qué:* sin lista de dominios permitidos, una cabecera `Host` manipulada altera los enlaces de restablecimiento de contraseña.

- [ ] **Redis para la limitación de tráfico**
    - *Por qué:* hoy el límite vive en la memoria del proceso. Con varios workers, cada uno lleva su propia cuenta y el límite efectivo se multiplica por el número de workers.

- [ ] **Root Directory = `ruralhealth`** en el servicio (Render, Railway, etc.)
    - *Por qué:* `uploads/`, `logs/` e `instance/` se resuelven contra el directorio de trabajo. Arrancar desde la raíz del repositorio crea esas carpetas en el sitio equivocado.

- [ ] **Copiar las variables de `ruralhealth/.env`** al panel del servicio
    - *Por qué:* el archivo no se sube al repositorio ni entra en la imagen Docker, a propósito.

---

## Deuda técnica documentada y sin resolver

Salió de la auditoría de farmacia. Lo dejé escrito y sin tocar porque son
cambios de alcance mayor que merecen tu decisión.

- [ ] **Las transferencias entre farmacias no mueven inventario**
    - *Qué pasa:* al aprobarlas solo se marca «completada». No hay decremento en la sede de origen ni incremento en la de destino.
    - *Consecuencia:* el medicamento viaja físicamente de A a B; el sistema sigue diciendo que está en A. El paciente va a A, donde ya no hay. El descuadre es permanente y crece con cada traslado.

- [ ] **Siete caminos cambian existencias sin dejar asiento en el libro mayor**
    - *Cuáles:* edición manual de inventario, creación de stock, recepción de cargamento, emisión de ticket, reactivación de pendiente, llegada de mercancía y cambio de sede.
    - *Consecuencia:* `manage.py verify-ledger` reportará descuadre desde el primer día. Para el servicio farmacéutico, un inventario sin trazabilidad de entradas y ajustes es un incumplimiento de la Resolución 1403 de 2007, no solo un defecto técnico.

- [ ] **`verify_chain` no recalcula el hash de cada asiento**
    - *Qué pasa:* compara el encadenamiento pero no recomputa el contenido. Alterar la cantidad de un asiento sin tocar sus columnas de hash pasa la verificación intacto.
    - *Agravante:* el hash es SHA-256 sin clave, así que quien tenga acceso a la base puede recalcular la cadena entera. Debería ser HMAC con la clave del servidor, como ya hace `security.py` para las órdenes.

- [ ] **Las reservas de un ticket no reclamado no caducan**
    - *Qué pasa:* `MedicationPickupTicket` no tiene fecha de caducidad y no hay ruta de cancelación. Un ticket que el paciente nunca reclama mantiene sus unidades apartadas para siempre.

- [ ] **Punto de reposición por medicamento y sede, sin fijar**
    - *Quién:* el prestador
    - *Qué pasa:* mientras esté en cero, el sistema solo avisa cuando el medicamento se agota. El panel de administración ordena la lista poniendo arriba lo que falta por configurar.

- [ ] **`billing.py` está desconectado de la aplicación**
    - *Qué pasa:* `build_draft`, `issue` y `annul` no se invocan desde ninguna ruta. El paciente paga, obtiene acceso, y no recibe factura.

---

## Antes de vender o de buscar inversión

- [ ] **Aclarar la propiedad intelectual con la universidad**
    - *Por qué:* el proyecto nació como trabajo académico. Lee el reglamento de propiedad intelectual de la UAO y consigue un paz y salvo o una cesión expresa por escrito **antes** de vincular el proyecto a un semillero o a un trabajo de grado. *Es lo primero que revisa el abogado del comprador.*

- [ ] **Constituir la persona jurídica** y ceder a su nombre los derechos
  patrimoniales.

- [ ] **Declarar el incidente de credenciales.** El `.env` con la semilla de
  cifrado estuvo en el repositorio. Ya está resuelto (base nueva, llaves
  nuevas), pero sale en cualquier revisión y es mejor que lo cuentes tú.

- [ ] **Una IPS real usando el sistema.** Es la palanca que más mueve la
  valoración, muy por encima de cualquier mejora de código.

---

## Ya está hecho, para que no lo repitas

- [x] **Rotación de credenciales** (8 sep 2026). Instancia PostgreSQL nueva y
  las cuatro llaves generadas desde cero, incluida `HASH_PEPPER`, que faltaba
  por completo. Se hizo con la base vacía, que es el único momento en que no
  cuesta nada.
- [x] Esquema aplicado sobre PostgreSQL limpio (57 tablas, revisión
  `d15a8e30c7b4`).
- [x] El motor de seguridad evalúa el principio activo, no la marca comercial.
- [x] Prescripción por denominación común internacional.
- [x] Reservas de existencias con dueño; el ticket ya no se resta a sí mismo.
- [x] Bloqueo del ticket en la entrega; no se dispensa dos veces.
- [x] No se dispensa contra órdenes anuladas o vencidas.
- [x] Cambiar la contraseña invalida sesiones y tokens anteriores.
- [x] Los documentos legales solo los edita la superadministración.
- [x] Orden médica impresa conforme al Decreto 2200 de 2005, artículo 17.
- [x] Rediseño de las 46 pantallas sobre un vocabulario único.
- [x] `python app.py --local` vuelve a arrancar.
- [x] Logo propio.

---

## Cómo comprobar dónde estás

```bash
cd ruralhealth
python manage.py preflight        # qué falta para desplegar
python -m pytest -q               # 573 pruebas
python scripts/recorrido/simular.py   # 52 acciones reales, siete roles
```
