/** @type {import('tailwindcss').Config} */

function withOpacity(varName) {
  return ({ opacityValue }) => {
    if (opacityValue !== undefined) {
      return `rgba(var(${varName}), ${opacityValue})`;
    }
    return `rgb(var(${varName}))`;
  };
}

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        'bg-primary': withOpacity('--bg-primary-rgb'),
        'bg-secondary': withOpacity('--bg-secondary-rgb'),
        'bg-card': withOpacity('--bg-card-rgb'),
        accent: withOpacity('--accent-rgb'),
        'accent-light': withOpacity('--accent-light-rgb'),
        'accent-dark': withOpacity('--accent-dark-rgb'),
        success: withOpacity('--success-rgb'),
        danger: withOpacity('--danger-rgb'),
        warning: withOpacity('--warning-rgb'),
        'text-primary': withOpacity('--text-primary-rgb'),
        'text-secondary': withOpacity('--text-secondary-rgb'),
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', '"Fira Code"', 'monospace'],
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
      },
    },
  },
  plugins: [],
};
