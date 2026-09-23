import { useAuth } from '@/hooks/useAuth';

export default function ConnectPrompt({ message }: { message?: string }) {
  const { login, isLoading } = useAuth();
  return (
    <div className="flex flex-col items-center justify-center gap-4 py-16 text-center">
      <div className="w-12 h-12 rounded-full bg-accent/10 border border-accent/20 flex items-center justify-center">
        <svg className="w-6 h-6 text-accent" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17 9V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-2M9 12h12l-3-3m0 6l3-3" />
        </svg>
      </div>
      <div>
        <div className="text-sm font-semibold text-text-primary">Connect your wallet</div>
        <p className="text-xs text-text-secondary mt-1 max-w-xs">
          {message || 'Connect to manage your watchlist and copy subscriptions.'}
        </p>
      </div>
      <button onClick={() => login()} disabled={isLoading} className="btn-primary text-sm px-5 py-2 disabled:opacity-50">
        {isLoading ? 'Connecting…' : 'Connect Wallet'}
      </button>
    </div>
  );
}
