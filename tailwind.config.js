/**
 * Sistema visual de RuralHealth Connect.
 *
 * Por qué esto vive aquí y no en un `<script>` de la plantilla
 * ------------------------------------------------------------
 * La aplicación cargaba Tailwind desde `cdn.tailwindcss.com`, que compila el CSS
 * en el navegador. Eso significaba tres cosas malas para este producto:
 *
 *   1. Sin acceso a ese CDN la interfaz se queda **sin ningún estilo**. No se
 *      degrada: se rompe. Y esta es una aplicación para zonas de baja
 *      conectividad, donde eso no es un caso raro sino el caso esperado.
 *   2. El script Play no admite verificación de integridad, así que un CDN
 *      comprometido ejecutaría JavaScript arbitrario sobre páginas con historia
 *      clínica abierta.
 *   3. Compilar en el navegador cuesta en cada carga, sobre el hardware de gama
 *      baja que este sistema declara como objetivo.
 *
 * Ahora el CSS se genera aquí y se sirve desde `/static/css/app.css`. El archivo
 * generado se versiona, así que para *ejecutar* la aplicación no hace falta Node
 * ni conexión: solo para regenerarlo tras cambiar estilos.
 *
 *   npm run build:css     una vez
 *   npm run watch:css     mientras se editan plantillas
 *
 * Regla del sistema: el color significa algo. Si todo tiene color, nada lo
 * tiene, y en una pantalla clínica eso es peligroso — la alerta de alergia debe
 * destacar sobre lo demás, no competir con un icono decorativo del mismo tono.
 */

module.exports = {
  // Tailwind solo genera las clases que encuentra usadas. Hay que mirar tanto
  // las plantillas como el JavaScript que construye marcado en tiempo de
  // ejecución, o esas clases faltarían del CSS final.
  content: [
    './templates/**/*.html',
    './static/**/*.js',
    // El vocabulario visual vive en Python (`ui.py`) y se inyecta en Jinja
    // como variables globales, asi que sus clases no aparecen escritas en
    // ninguna plantilla. Sin esta linea, el CSS saldria sin ellas.
    './ui.py',
  ],

  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },

      colors: {
        /* Acción principal. Azul clínico, no saturado. Una por pantalla. */
        accent: {
          DEFAULT: '#0369A1',
          hover: '#075985',
          subtle: '#F0F9FF',
          border: '#BAE6FD',
        },
        /* Riesgo para el paciente. Solo aquí. */
        critical: {
          DEFAULT: '#B91C1C',
          hover: '#991B1B',
          subtle: '#FEF2F2',
          border: '#FECACA',
          text: '#7F1D1D',
        },
        /* Requiere revisión del profesional, no bloqueo. */
        caution: {
          DEFAULT: '#B45309',
          subtle: '#FFFBEB',
          border: '#FDE68A',
          text: '#78350F',
        },
        /* Confirma que algo se completó. Nunca decorativo. */
        positive: {
          DEFAULT: '#047857',
          subtle: '#ECFDF5',
          border: '#A7F3D0',
          text: '#064E3B',
        },

        /* Estructura. Toda la jerarquía que no es semántica sale de aquí.
           Sin sombras, la única forma de separar un contenedor de otro es el
           contorno y el tono de fondo, así que los dos niveles tienen que
           distinguirse de verdad en una pantalla barata a plena luz. */
        linea: {
          /* Contorno por defecto de una tarjeta o una fila de tabla. */
          DEFAULT: '#CBD5E1',
          /* Contenedor principal, o el que está activo. */
          fuerte: '#334155',
          /* Separador interno: dentro de una tarjeta ya delimitada. */
          suave: '#E2E8F0',
        },
        lienzo: {
          /* Fondo de la aplicación, detrás de las tarjetas. */
          DEFAULT: '#F1F5F9',
          /* Cabecera de tabla y bandas de sección. */
          seccion: '#F5F5F7',
        },

        /* Compatibilidad con las clases que ya existían en las plantillas. */
        primary: '#0369A1',
        'primary-dark': '#075985',
        background: '#F8FAFC',
        medical: '#0369A1',
        'medical-dark': '#075985',
        'medical-light': '#F0F9FF',
      },

      minHeight: {
        /* Área táctil mínima. Se usa con guantes, de pie, en movimiento. */
        control: '2.75rem',
      },
      minWidth: {
        control: '2.75rem',
      },

      borderRadius: {
        /* Radios discretos: un expediente clínico no es una tarjeta social. */
        xl: '0.5rem',
        '2xl': '0.625rem',
        '3xl': '0.75rem',
      },

      fontSize: {
        /* 13px en lugar de 12px: se lee al sol, en pantalla barata, por
           personas de cualquier edad. */
        xs: ['0.8125rem', { lineHeight: '1.125rem' }],
        sm: ['0.875rem', { lineHeight: '1.25rem' }],
      },

      boxShadow: {
        /* Sin sombras. Ninguna.
         *
         * La sombra es profundidad simulada: dice "esto flota por encima de
         * aquello". En una pantalla clínica no hay nada que flote, y en la
         * pantalla barata de un puesto de salud a plena luz una sombra suave
         * no se ve — solo ensucia el borde. La jerarquía la dan el contorno,
         * el tono de fondo y el peso tipográfico, que sí sobreviven al sol.
         *
         * Se dejan definidas en cero en lugar de borrarlas: así una clase
         * `shadow-sm` heredada de una plantilla vieja no rompe el build, solo
         * deja de pintar. El menú desplegable conserva la suya porque ahí la
         * capa sí es real: hay contenido debajo.
         */
        none: 'none',
        sm: 'none',
        DEFAULT: 'none',
        md: 'none',
        lg: 'none',
        xl: 'none',
        '2xl': 'none',
        capa: '0 4px 12px -2px rgb(15 23 42 / 0.12)',
      },
    },
  },

  plugins: [],
};
