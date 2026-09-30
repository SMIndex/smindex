import { useEffect, type ReactNode } from 'react';

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  maxWidth?: string;
  // Real-money / form modals set these false: dismiss ONLY via × / Cancel.
  closeOnOverlayClick?: boolean;
  closeOnEscape?: boolean;
}

export default function Modal({
  isOpen,
  onClose,
  title,
  children,
  maxWidth = 'max-w-md',
  closeOnOverlayClick = true,
  closeOnEscape = true,
}: ModalProps) {
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => {
      document.body.style.overflow = '';
    };
  }, [isOpen]);

  useEffect(() => {
    function handleEscape(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    if (isOpen && closeOnEscape) {
      document.addEventListener('keydown', handleEscape);
      return () => document.removeEventListener('keydown', handleEscape);
    }
  }, [isOpen, closeOnEscape, onClose]);

  if (!isOpen) return null;

  return (
    // stopPropagation at the modal root: this component renders IN PLACE (no
    // portal), so without it every click inside bubbles up the React tree to
    // ancestor overlays (e.g. TraderProfileModal's onClick={onClose}) — that
    // was closing the parent modal, unmounting this one mid-typing.
    <div className="fixed inset-0 z-[90] flex items-center justify-center p-0 md:p-4" onClick={(e) => e.stopPropagation()}>
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={closeOnOverlayClick ? onClose : undefined}
      />
      <div
        className={`relative w-full h-full md:h-auto md:max-h-[calc(100dvh-2rem)] ${maxWidth} bg-bg-card md:border border-text-secondary/20 md:rounded-xl shadow-2xl overflow-y-auto`}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-text-secondary/10 sticky top-0 bg-bg-card z-10">
          <h2 className="text-lg font-semibold text-text-primary">{title}</h2>
          <button
            onClick={onClose}
            className="p-2 min-h-[44px] min-w-[44px] flex items-center justify-center rounded-md text-text-secondary hover:text-text-primary hover:bg-bg-secondary transition-colors"
          >
            <svg
              className="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>
        <div className="p-5 pb-[calc(6rem+env(safe-area-inset-bottom))] md:pb-5">{children}</div>
      </div>
    </div>
  );
}
