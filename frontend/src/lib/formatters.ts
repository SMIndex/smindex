export function formatUSD(n: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(n);
}

export function formatPrice(n: number, decimals: number = 2): string {
  return new Intl.NumberFormat('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(n);
}

export function formatPercent(n: number): string {
  const sign = n >= 0 ? '+' : '';
  return `${sign}${n.toFixed(2)}%`;
}

export function formatSize(n: number): string {
  if (Math.abs(n) >= 1) {
    return n.toFixed(4);
  }
  return n.toFixed(6);
}

export function shortenAddress(addr: string): string {
  if (!addr) return '';
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

export function displayName(username: string | null | undefined, addr: string): string {
  if (username) return `${username} (${shortenAddress(addr)})`;
  return shortenAddress(addr);
}

export function formatTimeAgo(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diffMs = now - then;

  if (diffMs < 0) return 'just now';

  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 60) return `${seconds}s ago`;

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;

  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;

  const months = Math.floor(days / 30);
  return `${months}mo ago`;
}

export function formatCompact(n: number): string {
  const abs = Math.abs(n);
  if (abs >= 1_000_000_000) {
    return `$${(n / 1_000_000_000).toFixed(2)}B`;
  }
  if (abs >= 1_000_000) {
    return `$${(n / 1_000_000).toFixed(2)}M`;
  }
  if (abs >= 1_000) {
    return `$${(n / 1_000).toFixed(1)}K`;
  }
  return `$${n.toFixed(2)}`;
}

export function severityColor(severity: string): string {
  switch (severity) {
    case 'extreme':
      return 'text-danger bg-danger/10 border-danger/30';
    case 'high':
      return 'text-warning bg-warning/10 border-warning/30';
    case 'medium':
      return 'text-accent bg-accent/10 border-accent/30';
    default:
      return 'text-text-secondary bg-text-secondary/10 border-text-secondary/30';
  }
}
