# Revisión de la base de conocimiento clínico · RuralHealth Connect

**Para:** químico farmacéutico
**De:** Juan D. Cuevas — RuralHealth Connect
**Versión de la base de conocimiento:** 2026.09.1
**Fecha del documento:** septiembre de 2026

---

## Qué es esto y qué le pido

RuralHealth Connect es una plataforma de atención en salud para zonas rurales
de Colombia. Cuando un médico va a firmar una orden, el sistema contrasta lo
que ha prescrito contra la historia del paciente y **puede bloquear la firma**.

Ese contraste sale de unas tablas que están escritas en el código. Yo las armé
con fuentes públicas y **no soy químico farmacéutico**. El propio módulo lo
declara así:

> «Revisión inicial de atención primaria. Requiere validación por químico
> farmacéutico.»

Lo que le pido es esa validación.

**No necesita leer código.** Este documento incluye las tablas completas en
formato legible, y puedo entregárselas en la hoja de cálculo que prefiera.

---

## Qué comprueba el sistema y qué hace con el resultado

Hay dos niveles de resultado, y la diferencia importa:

| Nivel | Qué ocurre |
| :--- | :--- |
| **Bloqueo** | El médico **no puede firmar** salvo que escriba una justificación de al menos 20 caracteres. La justificación queda archivada con la orden y auditada. |
| **Advertencia** | Se muestra, no bloquea. |

Las seis comprobaciones:

1. **Alergias.** Contrasta cada medicamento contra las alergias registradas del
   paciente, por principio activo y por familia farmacológica. → Bloqueo
2. **Interacciones** entre los medicamentos de la orden y los del tratamiento
   activo de los últimos 90 días. → Bloqueo o advertencia según el par
3. **Duplicidad terapéutica**: dos principios activos de la misma clase. →
   Bloqueo, salvo analgésicos y antihistamínicos, donde es advertencia
4. **Embarazo**: contraindicados y de precaución. → Bloqueo o advertencia
5. **Pediatría**: restricciones por edad. → Advertencia
6. **Cantidad**: topes por medicamento, con tope más estricto para control
   especial. → Advertencia

---

## Lo que hay hoy, en números

| Tabla | Entradas | Qué contiene |
| :--- | ---: | :--- |
| `DRUG_CLASSES` | 145 | Principio activo → clase terapéutica |
| `INTERACTIONS` | 40 | Pares de principios activos que interactúan |
| `CLASS_INTERACTIONS` | 5 | Interacciones entre clases completas |
| `PREGNANCY_CONTRAINDICATED` | 21 | Contraindicados en embarazo |
| `PREGNANCY_CAUTION` | 10 | De precaución en embarazo |
| `CROSS_REACTIVITY` | 7 | Reactividad cruzada entre familias |
| `CONTROLLED_SUBSTANCES` | 15 | Medicamentos de control especial |
| `SYNONYMS` | 28 | Marcas y sinónimos → principio activo |
| `CLASS_LABELS` | 15 | Nombres legibles de las clases |

Está pensado para **atención primaria rural**, no para hospitalización ni para
especialidades.

---

## Las preguntas concretas

### 1. ¿Qué falta que sea frecuente en atención primaria rural?

Es la pregunta más importante. Una tabla incompleta **no avisa**, y no avisar
se ve exactamente igual que «todo en orden».

Me interesa sobre todo: antihipertensivos, antidiabéticos, antibióticos de uso
ambulatorio, analgésicos, antiparasitarios y lo relacionado con dengue y
malaria, que son eventos frecuentes en las zonas donde va a operar.

### 2. ¿Está bien clasificado lo que hay?

Los 145 principios activos están asignados a una clase terapéutica. De esa
asignación dependen la detección de duplicidad y las interacciones por clase:
un medicamento en la clase equivocada genera alertas falsas o, peor, calla
donde debería avisar.

### 3. ¿Son correctas las 40 interacciones, y su severidad?

Cada par está marcado como bloqueante o como advertencia. Marcar como bloqueo
algo que en la práctica se prescribe junto a diario genera fatiga de alerta, y
el médico empieza a justificar sin leer. Marcar como advertencia algo grave es
el error contrario.

### 4. ¿Los topes de cantidad tienen sentido?

Hoy son:

- **180 unidades** como tope general
- **30 unidades** para medicamentos de control especial
- **1.000 unidades** como cifra absurda, que sí bloquea

Aquí hay una decisión pendiente que quiero consultarle. Hoy, superar las 30
unidades de un controlado produce **advertencia**, no bloqueo: 900 unidades de
tramadol salen con dos advertencias no bloqueantes y sin justificación escrita.
Mi impresión es que debería bloquear. Quiero su criterio.

### 5. ¿La lista de control especial está completa?

Son 15 hoy. Debería corresponder a lo que exige la **Resolución 1478 de 2006**
y sus modificaciones.

### 6. Embarazo y pediatría

¿Las 21 contraindicaciones y las 10 precauciones cubren lo que se ve en una
consulta rural? ¿Faltan restricciones pediátricas por edad?

### 7. ¿Qué NO debería intentar comprobar este sistema?

Igual de útil que lo anterior. Si algo exige criterio clínico que una tabla no
puede sustituir, prefiero quitarlo a dar una falsa sensación de verificación.

---

## Un defecto que ya se corrigió, por si le da contexto

Hasta hace poco el motor evaluaba el **nombre comercial** en vez del principio
activo. Recetando «Amoxal» a un paciente con alergia a la penicilina registrada
y confirmada, el sistema devolvía **cero hallazgos**: no saltaba la alergia, ni
la interacción, ni la duplicidad, ni el embarazo, ni la pediatría.

Y la orden quedaba archivada con un informe que decía «sin hallazgos», es
decir, con constancia escrita de que se había verificado.

Ya está corregido: se evalúa siempre el principio activo. Lo menciono porque
ilustra por qué le pido esta revisión — el sistema puede estar equivocado y
parecer que funciona.

---

## Cómo se mantiene esto en el tiempo

La base lleva versión y fecha. El sistema tiene un comando que informa de su
antigüedad, y cada orden médica archiva **con qué versión** fue evaluada. Si
dentro de dos años hay que auditar una prescripción, se puede saber qué sabía
el sistema en ese momento.

Si acepta la revisión, su nombre y su tarjeta profesional quedarían asociados a
la versión validada, que es lo que le da valor frente a una auditoría.

---

## Cómo devolvérmelo

Lo que más me sirve, en este orden:

1. **Lo que falta** y es frecuente en atención primaria rural.
2. **Lo que está mal clasificado o mal graduado** en severidad.
3. Su criterio sobre el tope de los controlados (pregunta 4).
4. Lo que sobra o no debería estar.

Puedo entregarle las nueve tablas en Excel o en CSV, como prefiera. Dígame y se
las paso en el formato que le sea más cómodo de anotar.
