/**
 * The design system.
 *
 * Colours are defined once here and referenced by class name everywhere else,
 * so the palette can be changed in one place. The brand is an original warm
 * rose/plum identity, deliberately not an imitation of any existing app.
 */
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./app/**/*.{js,jsx,ts,tsx}', './src/**/*.{js,jsx,ts,tsx}'],
  presets: [require('nativewind/preset')],
  // NativeWind's web runtime drives dark mode through a class on the root
  // element and observes it. Leaving this as Tailwind's default ('media')
  // makes that observer throw when the scheme changes.
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Primary: a warm rose, used for actions and the "like" affordance.
        rose: {
          50: '#FFF1F5',
          100: '#FFE3EB',
          200: '#FFC8D8',
          300: '#FF9DBA',
          400: '#FA6A95',
          500: '#E0366B',
          600: '#C42057',
          700: '#A11647',
          800: '#7D0F37',
          900: '#5A0A27',
        },
        // Secondary: plum, for gradients and accents.
        plum: {
          100: '#F3E9F7',
          300: '#D5B4E2',
          500: '#9B59B6',
          700: '#6B3184',
          900: '#3F1A52',
        },
        // Neutrals are warm-tinted rather than pure grey, which keeps the
        // interface from feeling clinical.
        ink: {
          50: '#FAF7F8',
          100: '#F2ECEF',
          200: '#E2D8DD',
          300: '#C9BAC2',
          400: '#9B8C94',
          500: '#75656D',
          600: '#5B4D55',
          700: '#41353C',
          800: '#2A2127',
          900: '#1C1419',
        },
        success: '#2E9E6B',
        warning: '#D98324',
        danger: '#D64545',
      },
      fontFamily: {
        // Loaded in app/_layout.tsx.
        display: ['Fraunces_600SemiBold', 'serif'],
        sans: ['Inter_400Regular', 'system-ui', 'sans-serif'],
        medium: ['Inter_500Medium', 'system-ui', 'sans-serif'],
        semibold: ['Inter_600SemiBold', 'system-ui', 'sans-serif'],
        bold: ['Inter_700Bold', 'system-ui', 'sans-serif'],
      },
      borderRadius: {
        card: '24px',
        pill: '999px',
      },
    },
  },
  plugins: [],
};
