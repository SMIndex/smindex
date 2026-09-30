import { useState, useCallback, type ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import Header, { MobileTabBar } from '@/components/layout/Header';
import Sidebar from '@/components/layout/Sidebar';
import BrandWatermarks from '@/components/layout/BrandWatermarks';
import Footer from '@/components/layout/Footer';
import { useKeyboardShortcuts } from '@/hooks/useKeyboardShortcuts';
import ShortcutHelpModal from '@/components/common/ShortcutHelpModal';
import { useDesignShell } from '@/design-b/useDesignShell';
import ShellB from '@/design-b/ShellB';

interface LayoutProps {
  children: ReactNode;
}

export default function Layout({ children }: LayoutProps) {
  const [showHelp, setShowHelp] = useState(false);
  const toggleHelp = useCallback(() => setShowHelp((v) => !v), []);
  useKeyboardShortcuts(toggleHelp);
  const location = useLocation();

  // Design B (wrapper Rule 1): behind ?design=b only. Default 'a' renders the
  // EXACT current shell below — real users see nothing change. Shell B swaps the
  // chrome (sidebar) around the same routed children; per-screen migration is
  // handled at the route level in App.tsx.
  const design = useDesignShell();
  if (design === 'b') {
    return <ShellB>{children}</ShellB>;
  }

  // Floating-card redesign: marks render app-wide (very subtle, shown in the gaps
  // between opaque cards). The blurred accent glow is reserved for the spacious copy
  // pages — off on the dense terminal so it never repaints behind the chart.
  const isCopy = location.pathname.startsWith('/copy');
  // /trade is a fixed full-height terminal that manages its own padding + has no footer.
  const isTrade = location.pathname.startsWith('/trade');

  return (
    // min-h-[100dvh] (via inline style, with min-h-screen class fallback for old browsers)
    // uses the DYNAMIC viewport so mobile/PWA content + bottom nav aren't cut off by the
    // URL bar / home indicator.
    <div className="min-h-screen flex flex-col relative" style={{ minHeight: '100dvh' }}>
      <BrandWatermarks glow={isCopy} />
      <Header />
      {/* z-[1] keeps content above the fixed watermark; the original height chain
          (root min-h-screen -> header + this flex-1 overflow-hidden row) is preserved
          so the terminal chart sizes correctly. */}
      <div className="relative z-[1] flex flex-1 overflow-hidden">
        {/* Trade is the dense terminal — markets live in its top-bar pills, so the
            global market sidebar is redundant there and stealing chart width. */}
        {!isTrade && <Sidebar />}
        <main className="flex-1 overflow-y-auto overflow-x-hidden" style={{ WebkitOverflowScrolling: 'touch' }}>
          {isTrade ? (
            <div key={location.pathname} className="animate-pageIn">{children}</div>
          ) : (
            <div key={location.pathname} className="flex flex-col min-h-full">
              {/* pb clears the mobile tab bar + home-indicator safe area; centered on ultra-wide. */}
              <div className="p-4 md:p-6 pb-[calc(6rem+env(safe-area-inset-bottom))] md:pb-6 w-full max-w-[1600px] mx-auto animate-pageIn flex-1">{children}</div>
              <Footer />
            </div>
          )}
        </main>
      </div>
      <MobileTabBar />
      {showHelp && <ShortcutHelpModal onClose={() => setShowHelp(false)} />}
    </div>
  );
}
