/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          50:  'var(--primary-50)',
          100: 'var(--primary-100)',
          200: 'var(--primary-200)',
          300: 'var(--primary-300)',
          400: 'var(--primary-400)',
          500: 'var(--primary-500)',
          600: 'var(--primary-600)',
          700: 'var(--primary-700)',
          800: 'var(--primary-800)',
          900: 'var(--primary-900)',
        },
        surface: {
          950: 'var(--surface-950)',
          0:   'var(--surface-0)',
          50:  'var(--surface-50)',
          100: 'var(--surface-100)',
          200: 'var(--surface-200)',
          300: 'var(--surface-300)',
          400: 'var(--surface-400)',
          500: 'var(--surface-500)',
          600: 'var(--surface-600)',
          700: 'var(--surface-700)',
          800: 'var(--surface-800)',
          900: 'var(--surface-900)',
        },
        green: {
          300: 'var(--green-300)', 400: 'var(--green-400)', 500: 'var(--green-500)',
          600: 'var(--green-600)', 950: 'var(--green-950)',
        },
        emerald: {
          300: 'var(--green-300)', 400: 'var(--green-400)', 500: 'var(--green-500)',
          600: 'var(--green-600)', 950: 'var(--green-950)',
        },
        red: {
          300: 'var(--red-300)', 400: 'var(--red-400)', 500: 'var(--red-500)', 600: 'var(--red-600)',
        },
        rose: {
          300: 'var(--red-300)', 400: 'var(--red-400)', 500: 'var(--red-500)', 600: 'var(--red-600)',
        },
        amber: {
          300: 'var(--amber-300)', 400: 'var(--amber-400)', 500: 'var(--amber-500)', 950: 'var(--amber-950)',
        },
        blue: {
          300: 'var(--blue-300)', 400: 'var(--blue-400)', 500: 'var(--blue-500)', 950: 'var(--blue-950)',
        },
        sky: {
          300: 'var(--blue-300)', 400: 'var(--blue-400)', 500: 'var(--blue-500)', 950: 'var(--blue-950)',
        },
        violet: {
          300: 'var(--violet-300)', 400: 'var(--violet-400)', 500: 'var(--violet-500)', 600: 'var(--violet-600)',
        },
        success: 'var(--color-success)',
        warning: 'var(--color-warning)',
        error:   'var(--color-error)',
        info:    'var(--color-info)',
      },
      fontFamily: {
        sans: ['Inter Variable', 'system-ui', 'sans-serif'],
        heading: ['Space Grotesk Variable', 'Inter Variable', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      fontSize: {
        'xs':   ['11px', { lineHeight: '16px', letterSpacing: '0.02em' }],
        'sm':   ['12px', { lineHeight: '18px' }],
        'base': ['13px', { lineHeight: '20px' }],
        'md':   ['14px', { lineHeight: '22px' }],
        'lg':   ['16px', { lineHeight: '24px' }],
        'xl':   ['18px', { lineHeight: '28px' }],
        '2xl':  ['22px', { lineHeight: '32px' }],
        '3xl':  ['28px', { lineHeight: '36px' }],
      },
      borderRadius: {
        'sm': '6px',
        DEFAULT: '8px',
        'md': '10px',
        'lg': '12px',
        'xl': '16px',
        '2xl': '20px',
      },
      boxShadow: {
        'card':       'var(--shadow-card)',
        'card-hover': 'var(--shadow-card-hover)',
        'popover':    'var(--shadow-popover)',
      },
      animation: {
        'fade-in':    'fadeIn 0.18s cubic-bezier(0.16, 1, 0.3, 1)',
        'slide-up':   'slideUp 0.18s cubic-bezier(0.16, 1, 0.3, 1)',
        'slide-in':   'slideIn 0.18s cubic-bezier(0.16, 1, 0.3, 1)',
        'pulse-soft': 'pulseSoft 2s cubic-bezier(0.65, 0, 0.35, 1) infinite',
        'shimmer':    'shimmer 1.4s cubic-bezier(0.65, 0, 0.35, 1) infinite',
      },
      keyframes: {
        fadeIn:    { from: { opacity: '0' }, to: { opacity: '1' } },
        slideUp:   { from: { opacity: '0', transform: 'translateY(8px)' }, to: { opacity: '1', transform: 'translateY(0)' } },
        slideIn:   { from: { opacity: '0', transform: 'translateX(-8px)' }, to: { opacity: '1', transform: 'translateX(0)' } },
        pulseSoft: { '0%, 100%': { opacity: '1' }, '50%': { opacity: '0.6' } },
        shimmer:   { '0%': { backgroundPosition: '-200% 0' }, '100%': { backgroundPosition: '200% 0' } },
      },
    },
  },
  plugins: [],
}
