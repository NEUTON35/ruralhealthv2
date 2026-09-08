# Revisión de la base de conocimiento clínico · RuralHealth Connect

**Para:** químico farmacéutico
**De:** Juan David Gómez Aragón — RuralHealth Connect
**Contacto:** juan.gomezaragon@uao.edu.co · +57 305 460 9909
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

**No necesita leer código, ni acceder a ningún sistema.** Las nueve tablas van
completas al final de este mismo documento, en el anexo. Si prefiere anotarlas
en una hoja de cálculo, se las paso en Excel o en CSV; dígame cuál.

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

Las nueve tablas están en el anexo. Si prefiere anotarlas en una hoja de
cálculo, dígame y se las paso en Excel o en CSV.

---

<!-- ANEXO GENERADO: no editar a mano, sale de generar_anexos.py -->

# Anexo · Las nueve tablas, completas

Esto es lo que hay que revisar. Va aquí dentro para no tener que entrar a ningún sistema ni leer código.

**Versión de la base de conocimiento: 2026.09.1**

---

## 1. Principios activos por clase terapéutica (145)

De esta clasificación dependen la detección de duplicidad y las interacciones por clase. Un medicamento en la clase equivocada genera alertas falsas o, peor, calla donde debería avisar.

| Clase | Principios activos |
| :--- | :--- |
| antiinflamatorios no esteroideos (AINE) | acido acetilsalicilico, celecoxib, diclofenaco, ibuprofeno, indometacina, ketoprofeno, ketorolaco, meloxicam, naproxeno, piroxicam |
| aminoglucosidos | amikacina, estreptomicina, gentamicina |
| analgesicos antipireticos | dipirona, metamizol, paracetamol |
| antiagregantes | clopidogrel |
| antiarritmicos | amiodarona |
| anticoagulantes | apixaban, dabigatran, enoxaparina, heparina, rivaroxaban, warfarina |
| anticolinergicos inhalados | ipratropio |
| anticonvulsivantes | acido valproico, carbamazepina, fenitoina, levetiracetam |
| antidepresivos triciclicos | amitriptilina, imipramina |
| antih2 | famotidina, ranitidina |
| antihistaminicos | cetirizina, clorfeniramina, difenhidramina, loratadina |
| antimalaricos | artemeter, cloroquina, lumefantrina, primaquina |
| antiparasitarios | albendazol, ivermectina, mebendazol, praziquantel |
| antipsicoticos | haloperidol, olanzapina, quetiapina, risperidona |
| antagonistas del receptor de angiotensina II | irbesartan, losartan, valsartan |
| barbituricos | fenobarbital |
| benzodiacepinas | alprazolam, clonazepam, diazepam, lorazepam, midazolam |
| beta2 agonistas | formoterol, salbutamol, salmeterol |
| betabloqueadores | atenolol, carvedilol, metoprolol, propranolol |
| biguanidas | metformina |
| calcioantagonistas | amlodipino, diltiazem, nifedipino, verapamilo |
| carbapenemicos | ertapenem, imipenem, meropenem |
| cefalosporinas | cefalexina, cefazolina, cefepime, cefotaxima, cefradina, ceftazidima, ceftriaxona, cefuroxima |
| corticoides inhalados | beclometasona, budesonida |
| corticoides sistemicos | dexametasona, hidrocortisona, metilprednisolona, prednisolona, prednisona |
| digitalicos | digoxina |
| diureticos ahorradores potasio | espironolactona |
| diureticos asa | furosemida |
| diureticos tiazidicos | hidroclorotiazida |
| estabilizadores animo | litio |
| estatinas | atorvastatina, lovastatina, pravastatina, rosuvastatina, simvastatina |
| glucopeptidos | vancomicina |
| hormonas tiroideas | levotiroxina |
| inhibidores de la ECA | captopril, enalapril, lisinopril |
| inhibidores bomba protones | esomeprazol, omeprazol, pantoprazol |
| insulinas | insulina |
| isrn | duloxetina, venlafaxina |
| inhibidores selectivos de recaptacion de serotonina | citalopram, escitalopram, fluoxetina, paroxetina, sertralina |
| lincosamidas | clindamicina |
| macrolidos | azitromicina, claritromicina, eritromicina |
| nitrofuranos | nitrofurantoina |
| nitroimidazoles | metronidazol |
| opioides | codeina, fentanilo, hidrocodona, meperidina, metadona, morfina, oxicodona, tramadol |
| penicilinas | amoxicilina, ampicilina, dicloxacilina, oxacilina, penicilina, piperacilina |
| quinolonas | ciprofloxacino, levofloxacino, moxifloxacino, norfloxacino |
| sulfas | sulfadiazina, sulfametoxazol, sulfasalazina |
| sulfonilureas | glibenclamida, glimepirida |
| tetraciclinas | doxiciclina, tetraciclina |

---

## 2. Interacciones entre principios activos (40)

Marcar como bloqueo algo que se prescribe junto a diario genera fatiga de alerta, y el médico empieza a justificar sin leer. Marcar como advertencia algo grave es el error contrario.

| Principio A | Principio B | Efecto | Mecanismo | Consecuencia clínica |
| :--- | :--- | :--- | :--- | :--- |
| acido acetilsalicilico | warfarina | BLOQUEA | Suma de efecto anticoagulante y antiagregante, con desplazamiento de la union a proteinas plasmaticas. | Riesgo de hemorragia mayor. Si la combinacion es necesaria, definir indicacion, ajustar dosis y controlar INR de forma estrecha. |
| alcohol | metronidazol | Advierte | Inhibicion de la aldehido deshidrogenasa. | Reaccion tipo disulfiram. Advertir al paciente que evite alcohol durante el tratamiento y 48 horas despues. |
| alprazolam | tramadol | Advierte | Depresion aditiva del sistema nervioso central. | Advertir sobre sedacion y no conducir. |
| amiodarona | digoxina | BLOQUEA | La amiodarona reduce el aclaramiento de la digoxina. | Riesgo de intoxicacion digitalica. Reducir la digoxina a la mitad y medir niveles. |
| amiodarona | levofloxacino | BLOQUEA | Prolongacion aditiva del intervalo QT. | Riesgo de torsade de pointes. Evitar la combinacion. |
| amiodarona | moxifloxacino | BLOQUEA | Prolongacion aditiva del intervalo QT. | Riesgo de torsade de pointes. Evitar la combinacion. |
| amiodarona | simvastatina | BLOQUEA | Inhibicion del metabolismo de la estatina. | Riesgo de miopatia y rabdomiolisis. Limitar simvastatina a 20 mg/dia. |
| amitriptilina | fluoxetina | Advierte | Inhibicion del CYP2D6 con aumento de la concentracion del triciclico. | Vigilar toxicidad anticolinergica y prolongacion del QT. |
| amitriptilina | tramadol | Advierte | Suma serotoninergica y descenso del umbral convulsivo. | Vigilar sintomas de sindrome serotoninergico. |
| atorvastatina | claritromicina | Advierte | Inhibicion del CYP3A4 con aumento de la concentracion de la estatina. | Vigilar mialgias; considerar reducir dosis de la estatina. |
| carbamazepina | fenitoina | Advierte | Induccion enzimatica reciproca. | Ambas concentraciones pueden caer. Medir niveles. |
| carbonato de calcio | ciprofloxacino | Advierte | Quelacion con cationes divalentes. | Perdida de eficacia del antibiotico. Separar las tomas 2 horas antes o 6 despues. |
| carbonato de calcio | doxiciclina | Advierte | Quelacion con cationes divalentes. | Perdida de eficacia. Separar las tomas. |
| carbonato de calcio | levotiroxina | Advierte | Quelacion en la luz intestinal. | Separar las tomas al menos 4 horas. |
| ciprofloxacino | tizanidina | BLOQUEA | Inhibicion potente del CYP1A2. | Hipotension y sedacion graves. Combinacion contraindicada. |
| claritromicina | simvastatina | BLOQUEA | Inhibicion potente del CYP3A4 con acumulacion de la estatina. | Riesgo de rabdomiolisis. Suspender la estatina durante el tratamiento antibiotico. |
| clopidogrel | omeprazol | Advierte | El omeprazol inhibe el CYP2C19 y reduce la activacion del clopidogrel. | Perdida de eficacia antiagregante. Preferir pantoprazol. |
| diazepam | morfina | BLOQUEA | Depresion respiratoria aditiva del sistema nervioso central. | Riesgo de parada respiratoria. Evitar la combinacion en el ambito ambulatorio. |
| diclofenaco | warfarina | BLOQUEA | Los AINE inhiben la agregacion plaquetaria y lesionan la mucosa gastrica. | Riesgo de hemorragia digestiva. Preferir paracetamol como analgesico. |
| digoxina | furosemida | Advierte | La hipopotasemia inducida por el diuretico aumenta la toxicidad digitalica. | Controlar potasio y magnesio. |
| enalapril | espironolactona | BLOQUEA | Retencion aditiva de potasio. | Riesgo de hiperpotasemia grave y arritmia. Controlar potasio y creatinina. |
| enalapril | ibuprofeno | Advierte | Los AINE reducen la sintesis renal de prostaglandinas. | Perdida de control tensional y riesgo de lesion renal aguda, mayor si hay diuretico. |
| enalapril | litio | Advierte | Reduccion de la excrecion renal de litio. | Medir litemia tras iniciar. |
| enalapril | losartan | BLOQUEA | Doble bloqueo del sistema renina-angiotensina. | Aumenta hiperpotasemia, hipotension y dano renal sin beneficio demostrado. |
| espironolactona | ibuprofeno | Advierte | Los AINE reducen la excrecion de potasio. | Riesgo de hiperpotasemia. Controlar potasio. |
| espironolactona | losartan | BLOQUEA | Retencion aditiva de potasio. | Riesgo de hiperpotasemia grave. Controlar potasio y creatinina. |
| fluconazol | warfarina | Advierte | Inhibicion del CYP2C9. | Vigilar INR durante el tratamiento antifungico. |
| fluoxetina | tramadol | BLOQUEA | Suma de actividad serotoninergica. | Riesgo de sindrome serotoninergico y descenso del umbral convulsivo. |
| furosemida | metformina | Advierte | La deplecion de volumen puede deteriorar la funcion renal. | Riesgo de acidosis lactica si cae el filtrado glomerular. Controlar creatinina. |
| haloperidol | levofloxacino | Advierte | Prolongacion aditiva del intervalo QT. | Valorar electrocardiograma antes de iniciar. |
| hidroclorotiazida | litio | BLOQUEA | Las tiazidas aumentan la reabsorcion tubular de litio. | Riesgo de intoxicacion por litio. Medir litemia. |
| ibuprofeno | litio | BLOQUEA | Los AINE reducen la excrecion renal de litio. | Riesgo de intoxicacion por litio. Medir litemia. |
| ibuprofeno | prednisona | Advierte | Lesion aditiva de la mucosa gastrica. | Riesgo de ulcera y hemorragia digestiva. Valorar gastroproteccion. |
| ibuprofeno | warfarina | BLOQUEA | Los AINE inhiben la agregacion plaquetaria y lesionan la mucosa gastrica. | Riesgo de hemorragia digestiva. Preferir paracetamol como analgesico. |
| levotiroxina | omeprazol | Advierte | El aumento del pH gastrico reduce la absorcion de levotiroxina. | Separar las tomas al menos 4 horas. |
| metronidazol | warfarina | BLOQUEA | Inhibicion del metabolismo de la warfarina. | Elevacion del INR con riesgo de sangrado. Controlar INR. |
| naproxeno | warfarina | BLOQUEA | Los AINE inhiben la agregacion plaquetaria y lesionan la mucosa gastrica. | Riesgo de hemorragia digestiva. Preferir paracetamol como analgesico. |
| paroxetina | tramadol | BLOQUEA | Suma de actividad serotoninergica. | Riesgo de sindrome serotoninergico y descenso del umbral convulsivo. |
| sertralina | tramadol | BLOQUEA | Suma de actividad serotoninergica. | Riesgo de sindrome serotoninergico y descenso del umbral convulsivo. |
| sulfametoxazol | warfarina | BLOQUEA | Inhibicion del CYP2C9 con aumento marcado del efecto de la warfarina. | Elevacion importante del INR. Si no hay alternativa, controlar INR a las 72 horas. |

---

## 3. Interacciones entre clases completas (5)

| Clase A | Clase B | Efecto | Mecanismo | Consecuencia clínica |
| :--- | :--- | :--- | :--- | :--- |
| antiinflamatorios no esteroideos (AINE) | anticoagulantes | BLOQUEA | Los AINE inhiben la agregacion plaquetaria y lesionan la mucosa gastrica. | Riesgo de hemorragia mayor sobre anticoagulacion. Preferir paracetamol. |
| antiagregantes | anticoagulantes | Advierte | Efecto antitrombotico aditivo. | Solo con indicacion explicita y por el tiempo minimo necesario. |
| antagonistas del receptor de angiotensina II | inhibidores de la ECA | BLOQUEA | Doble bloqueo del sistema renina-angiotensina. | Aumenta hiperpotasemia y dano renal sin beneficio demostrado. |
| benzodiacepinas | opioides | BLOQUEA | Depresion respiratoria aditiva. | Combinacion asociada a mortalidad por sobredosis. Evitar en ambulatorio. |
| isrn | inhibidores selectivos de recaptacion de serotonina | BLOQUEA | Suma de actividad serotoninergica. | Riesgo de sindrome serotoninergico. |

---

## 4. Contraindicados en embarazo (21)

| Principio activo | Motivo |
| :--- | :--- |
| acido valproico | Riesgo elevado de defectos del tubo neural y deficit cognitivo. |
| atorvastatina | Las estatinas estan contraindicadas: el colesterol es esencial para el desarrollo fetal. |
| captopril | Los IECA producen dano renal fetal y oligohidramnios en 2.o y 3.er trimestre. |
| carbamazepina | Riesgo de defectos del tubo neural. |
| doxiciclina | Las tetraciclinas afectan el desarrollo oseo y dental fetal. |
| enalapril | Los IECA producen dano renal fetal y oligohidramnios en 2.o y 3.er trimestre. |
| fenitoina | Sindrome de hidantoina fetal. |
| finasterida | Riesgo de anomalias genitales en feto masculino. |
| irbesartan | Los ARA II producen dano renal fetal y oligohidramnios. |
| isotretinoina | Teratogeno mayor. Contraindicacion absoluta. |
| lisinopril | Los IECA producen dano renal fetal y oligohidramnios en 2.o y 3.er trimestre. |
| losartan | Los ARA II producen dano renal fetal y oligohidramnios. |
| lovastatina | Las estatinas estan contraindicadas en el embarazo. |
| metotrexato | Abortivo y teratogeno. |
| misoprostol | Induce contracciones uterinas y aborto. |
| ribavirina | Teratogeno. |
| rosuvastatina | Las estatinas estan contraindicadas en el embarazo. |
| simvastatina | Las estatinas estan contraindicadas en el embarazo. |
| tetraciclina | Las tetraciclinas afectan el desarrollo oseo y dental fetal. |
| valsartan | Los ARA II producen dano renal fetal y oligohidramnios. |
| warfarina | Embriopatia por warfarina; teratogeno en el primer trimestre. |

---

## 5. De precaución en embarazo (10)

| Principio activo | Motivo |
| :--- | :--- |
| acido acetilsalicilico | Salvo dosis baja indicada para prevencion de preeclampsia. |
| ciprofloxacino | Las quinolonas se evitan por efecto sobre el cartilago en desarrollo. |
| diclofenaco | Los AINE se evitan despues de la semana 20 por oligohidramnios y cierre prematuro del ductus arterioso. |
| fluconazol | Dosis altas y prolongadas se asocian a malformaciones. |
| ibuprofeno | Los AINE se evitan despues de la semana 20 por oligohidramnios y cierre prematuro del ductus arterioso. |
| levofloxacino | Las quinolonas se evitan por efecto sobre el cartilago en desarrollo. |
| litio | Anomalia de Ebstein; requiere valoracion especializada. |
| metronidazol | Evitar en el primer trimestre si existe alternativa. |
| naproxeno | Los AINE se evitan despues de la semana 20 por oligohidramnios y cierre prematuro del ductus arterioso. |
| sulfametoxazol | Antagonismo del folato en el primer trimestre; riesgo de kernicterus al termino. |

---

## 6. Reactividad cruzada entre familias (7)

Es lo que hace que una alergia registrada a una familia bloquee también otra emparentada.

| Familia | Reacciona también con | Efecto | Motivo |
| :--- | :--- | :--- | :--- |
| antiinflamatorios no esteroideos (AINE) | antiinflamatorios no esteroideos (AINE) | BLOQUEA | Reactividad cruzada entre AINE por inhibicion compartida de la ciclooxigenasa. Riesgo de broncoespasmo y de reaccion anafilactoide. |
| cefalosporinas | penicilinas | BLOQUEA | Reactividad cruzada betalactamica descrita entre cefalosporinas y penicilinas. |
| cefalosporinas | carbapenemicos | Advierte | Reactividad cruzada betalactamica de baja frecuencia con carbapenemicos. |
| macrolidos | macrolidos | BLOQUEA | Misma familia de macrolidos. |
| opioides | opioides | Advierte | Reactividad cruzada variable entre opioides; distinguir alergia verdadera de efecto adverso previsible (nausea, prurito por liberacion de histamina). |
| penicilinas | cefalosporinas | BLOQUEA | Reactividad cruzada betalactamica descrita entre penicilinas y cefalosporinas, mayor con cefalosporinas de primera generacion. |
| penicilinas | carbapenemicos | Advierte | Reactividad cruzada betalactamica de baja frecuencia con carbapenemicos. |
| quinolonas | quinolonas | BLOQUEA | Misma familia de quinolonas. |
| sulfas | sulfas | BLOQUEA | Misma familia de sulfonamidas. |

---

## 7. Medicamentos de control especial (15)

Debería corresponder a lo que exige la Resolución 1478 de 2006 y sus modificaciones. Hoy superar el tope de estos produce advertencia, no bloqueo: es la pregunta 4 del documento.

| Principio activo | Nota |
| :--- | :--- |
| alprazolam | Psicotropico |
| clonazepam | Psicotropico |
| codeina | Estupefaciente |
| diazepam | Psicotropico |
| fenobarbital | Psicotropico |
| fentanilo | Estupefaciente |
| hidrocodona | Estupefaciente |
| lorazepam | Psicotropico |
| meperidina | Estupefaciente |
| metadona | Estupefaciente |
| metilfenidato | Psicotropico |
| midazolam | Psicotropico |
| morfina | Estupefaciente |
| oxicodona | Estupefaciente |
| tramadol | Sujeto a control especial |

---

## 8. Marcas y sinónimos (28)

Lo que permite reconocer un principio activo cuando se escribe con otro nombre. Si falta una marca de uso corriente, el sistema no la reconoce y no avisa de nada.

| Se escribe | Se entiende como |
| :--- | :--- |
| aas | acido acetilsalicilico |
| acetaminofen | paracetamol |
| acetaminofeno | paracetamol |
| acetilcisteina | n acetilcisteina |
| advil | ibuprofeno |
| albuterol | salbutamol |
| amoxicilina acido clavulanico | amoxicilina |
| amoxiclav | amoxicilina |
| asa | acido acetilsalicilico |
| aspirina | acido acetilsalicilico |
| bencilpenicilina | penicilina |
| cotrimoxazol | sulfametoxazol |
| diclofenaco potasico | diclofenaco |
| diclofenaco sodico | diclofenaco |
| dolex | paracetamol |
| ibuprofeno | ibuprofeno |
| insulina cristalina | insulina |
| insulina glargina | insulina |
| insulina nph | insulina |
| losartan potasico | losartan |
| metformina clorhidrato | metformina |
| omeprazol sodico | omeprazol |
| penicilina benzatinica | penicilina |
| penicilina g | penicilina |
| salbutamol sulfato | salbutamol |
| tmp smx | sulfametoxazol |
| trimetoprim sulfametoxazol | sulfametoxazol |
| tylenol | paracetamol |

---

## 9. Topes de cantidad

| Tope | Unidades | Efecto |
| :--- | :--- | :--- |
| General | 180 | Advierte |
| Control especial | 30 | Advierte |
| Cifra absurda | 1000 | BLOQUEA |

<!-- FIN DEL ANEXO GENERADO -->
