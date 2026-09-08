# Mensajes para acompañar cada entregable

Para copiar y pegar. Ajusta lo que está entre corchetes.

Tres cosas que valen para los tres mensajes:

- **Di que eres estudiante.** Es verdad, se va a saber, y a la mayoría de
  profesionales les predispone bien. Disfrazarlo de empresa cuando la primera
  pregunta va a ser «¿y quién más está en el equipo?» juega en tu contra.
- **Pide algo concreto y acotado.** «Revísame esto» no se responde. «Necesito
  que decidas estas seis cosas» sí.
- **Di cuánto tiempo le va a costar.** Es lo primero que calcula quien lo lee,
  y si no se lo dices, calcula de más y no contesta.

---

## 1 · Abogado

**Asunto:** Revisión de documentos legales de una plataforma de salud rural (5 textos, 6 decisiones)

Buenos días, [nombre].

Soy Juan David Gómez Aragón, estudiante de Ingeniería Mecatrónica en la Universidad Autónoma de
Occidente. Desarrollé una plataforma de atención en salud para zonas rurales
que gestiona historia clínica, teleconsulta y dispensación de medicamentos, y
que remite la información obligatoria al Ministerio de Salud. Empezó como un
trabajo de clase y terminó siendo un sistema completo.

Los cinco textos legales ya están redactados: política de tratamiento de datos,
términos y condiciones, aviso de privacidad, consentimiento de telemedicina y
transparencia. Tienen la estructura del artículo 13 del Decreto 1377 de 2013 y
citan su fundamento norma por norma.

**Lo que necesito no es que los escriba, sino que decida seis cosas que no son
técnicas** y que yo no puedo resolver: la cláusula de limitación de
responsabilidad, cuál de las tres excepciones del artículo 26 de la Ley 1581 se
invoca para la transferencia internacional, el contrato de transmisión con cada
IPS, el régimen del profesional independiente como responsable autónomo, el
procedimiento de acreditación de la patria potestad para menores, y el
consentimiento informado asistencial de la Ley 23 de 1981.

Adjunto un documento de cuatro páginas donde está cada punto con su motivo, más
una sección de lo que ya quedó verificado para que no lo revise dos veces. No
necesita leer código.

Calculo que son entre dos y cuatro horas de trabajo. [Dime tus honorarios y
cómo prefieres que lo formalicemos. / Es un proyecto académico sin ánimo de
lucro y estoy consultando si el consultorio jurídico puede tomarlo.]

Quedo atento.

Juan David Gómez Aragón
juan.gomezaragon@uao.edu.co · +57 305 460 9909
juan.david.gomez.aragon@gmail.com

---

## 2 · Químico farmacéutico

**Asunto:** Validación de una base de conocimiento farmacológico (atención primaria rural)

Buenos días, [nombre].

Soy Juan David Gómez Aragón, estudiante de Ingeniería Mecatrónica en la Universidad Autónoma de
Occidente. Desarrollé una plataforma de atención en salud para zonas rurales, que
empezó como un trabajo de clase y terminó siendo un sistema completo.

Cuando un médico va a firmar una orden, el sistema contrasta lo prescrito
contra la historia del paciente y **puede bloquear la firma**: alergias,
interacciones, duplicidad terapéutica, embarazo, pediatría y cantidades.

Ese contraste sale de unas tablas que armé con fuentes públicas. **Yo no soy
químico farmacéutico**, y el propio módulo lo declara así: «revisión inicial de
atención primaria, requiere validación por químico farmacéutico».

Le escribo para pedirle esa validación. Son 145 principios activos clasificados
por familia, 40 interacciones con su severidad, 21 contraindicaciones en
embarazo y 15 medicamentos de control especial.

**La pregunta que más me importa es qué falta**, porque una tabla incompleta no
avisa, y no avisar se ve exactamente igual que «todo en orden». Sobre todo en
lo que es frecuente en zona rural: antihipertensivos, antidiabéticos,
antibióticos ambulatorios, dengue y malaria.

Adjunto un documento de cinco páginas con las siete preguntas concretas.
**No necesita leer código**: puedo pasarle las nueve tablas en Excel o en CSV,
como le sea más cómodo de anotar.

Si acepta, su nombre y su tarjeta profesional quedarían asociados a la versión
validada, que es lo que le da valor frente a una auditoría. El sistema guarda
con qué versión de la base se evaluó cada orden médica.

Calculo entre tres y seis horas. [Dime tus honorarios. / Es un proyecto
académico y estoy consultando si puede plantearse como trabajo conjunto.]

Quedo atento.

Juan David Gómez Aragón
juan.gomezaragon@uao.edu.co · +57 305 460 9909
juan.david.gomez.aragon@gmail.com

---

## 3 · Representante legal de la IPS

**Asunto:** Trámites necesarios para poner en marcha la plataforma

Buenos días, [nombre].

Adjunto el listado de trámites que hacen falta para que la plataforma pueda
operar. Son cinco, ninguno lo puede hacer el software: todos exigen que los
solicite un prestador habilitado, con su NIT y su código de habilitación.

**El primero es el urgente y bloquea todo lo demás:** las credenciales del IHCE,
que se piden en Hércules (SISPRO). La Resolución 1888 de 2025 obliga a remitir
un Registro Digital de Atención por cada atención en salud. No es un requisito
futuro; es una obligación en curso, y operar sin remitirlo es incumplimiento
desde la primera consulta.

Lo bueno es que del lado técnico ya está todo construido: la generación del
Bundle FHIR, la autenticación y la cola de reintentos. Lo que falta son ocho
datos que solo el prestador puede pedir. **Sugiero solicitarlas primero para el
entorno de pruebas**, que permite validar el envío completo sin tocar
producción.

Los otros cuatro pueden avanzar en paralelo: catálogos oficiales CIE-10 y CUPS
del SISPRO, facturación electrónica ante la DIAN, credenciales de MIPRES, y una
determinación sobre si hay obligación de registro ante el RNBD.

En el documento está cada uno con qué se pide, dónde, y qué pasa si falta.

Quedo atento a lo que necesite de mi parte.

Juan David Gómez Aragón
juan.gomezaragon@uao.edu.co · +57 305 460 9909
juan.david.gomez.aragon@gmail.com

---

## Versión corta, para un primer contacto

Cuando escribes por WhatsApp o LinkedIn y todavía no sabes si le interesa. La
idea es que responda «cuéntame», no que decida ahí mismo.

**Al abogado:**

> Buenos días, [nombre]. Soy Juan David Gómez Aragón, estudiante de la Autónoma.
> Desarrollé una plataforma de salud para zonas rurales y tengo cinco
> documentos legales redactados (política de datos, términos, aviso de
> privacidad, consentimiento de telemedicina y transparencia) que necesitan
> revisión de un abogado, más seis decisiones puntuales que no puedo tomar yo.
> ¿Le puedo enviar el detalle para que lo mire y me diga si le interesa?

**Al químico farmacéutico:**

> Buenos días, [nombre]. Soy Juan David Gómez Aragón, estudiante de la Autónoma.
> Desarrollé una plataforma de salud rural que verifica alergias e
> interacciones antes de que el médico firme una orden. Las tablas las armé yo
> con fuentes públicas y necesitan validación de un químico farmacéutico.
> ¿Le puedo enviar el detalle para que lo mire y me diga si le interesa?

**A una IPS, para proponer un piloto:**

> Buenos días, [nombre]. Soy Juan David Gómez Aragón, estudiante de la Autónoma.
> Desarrollé una plataforma que implementa el Registro Digital de Atención de
> la Resolución 1888 de 2025, que ya está vigente y que muchas IPS pequeñas aún
> no tienen resuelto. Estoy buscando una institución para un piloto.
> ¿Tendría quince minutos para que se lo muestre?

Ese último es el que más importa. No entras pidiendo ayuda: entras con algo que
ellos necesitan resolver de todos modos.
