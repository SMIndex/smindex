import { clsx } from 'clsx';

function SkeletonBase({ className }: { className?: string }) {
  return (
    <div
      className={clsx(
        'bg-text-secondary/10 rounded animate-pulse',
        className,
      )}
    />
  );
}

export function SkeletonText({ width = 'w-full', className }: { width?: string; className?: string }) {
  return <SkeletonBase className={clsx('h-3', width, className)} />;
}

export function SkeletonCard({ className }: { className?: string }) {
  return (
    <div className={clsx('card space-y-3', className)}>
      <SkeletonBase className="h-4 w-1/3" />
      <SkeletonBase className="h-3 w-full" />
      <SkeletonBase className="h-3 w-2/3" />
      <SkeletonBase className="h-8 w-1/2 mt-2" />
    </div>
  );
}

export function SkeletonTable({ rows = 5, cols = 4, className }: { rows?: number; cols?: number; className?: string }) {
  return (
    <div className={clsx('space-y-2', className)}>
      {/* Header */}
      <div className="flex gap-4 px-3 py-2">
        {Array.from({ length: cols }).map((_, i) => (
          <SkeletonBase key={i} className="h-3 flex-1" />
        ))}
      </div>
      {/* Rows */}
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex gap-4 px-3 py-2.5">
          {Array.from({ length: cols }).map((_, c) => (
            <SkeletonBase key={c} className="h-3 flex-1" />
          ))}
        </div>
      ))}
    </div>
  );
}

export function SkeletonChart({ className }: { className?: string }) {
  return (
    <div className={clsx('card p-0 overflow-hidden', className)}>
      <div className="px-4 py-3 border-b border-text-secondary/10 flex items-center gap-3">
        <SkeletonBase className="h-4 w-24" />
        <SkeletonBase className="h-4 w-16 ml-auto" />
      </div>
      <div className="p-4 flex items-end gap-1 h-[220px]">
        {Array.from({ length: 30 }).map((_, i) => (
          <SkeletonBase
            key={i}
            className="flex-1"
            style={{ height: `${30 + Math.random() * 60}%` } as React.CSSProperties}
          />
        ))}
      </div>
    </div>
  );
}

/** Full-page portfolio skeleton */
export function PortfolioSkeleton() {
  return (
    <div className="space-y-4 animate-fadeIn">
      <div className="flex items-center justify-between">
        <SkeletonBase className="h-6 w-32" />
        <SkeletonBase className="h-8 w-24 rounded-lg" />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
        <SkeletonCard />
        <SkeletonCard />
        <SkeletonCard />
      </div>
      <SkeletonChart />
      <SkeletonTable rows={4} cols={5} />
    </div>
  );
}

/** Full-page trade skeleton */
export function TradeSkeleton() {
  return (
    <div className="flex flex-col h-[calc(100vh-56px)] animate-fadeIn">
      {/* Top bar */}
      <div className="flex items-center px-3 py-2 bg-bg-secondary border-b border-text-secondary/10 gap-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <SkeletonBase key={i} className="h-6 w-16 rounded" />
        ))}
        <div className="ml-4 flex gap-6">
          <SkeletonBase className="h-4 w-20" />
          <SkeletonBase className="h-4 w-20" />
          <SkeletonBase className="h-4 w-20" />
        </div>
      </div>
      {/* Main grid */}
      <div className="flex-1 flex flex-col md:grid md:grid-cols-12 gap-0.5 bg-text-secondary/5 p-0.5">
        <div className="md:col-span-8 bg-bg-card p-4">
          <SkeletonBase className="w-full h-full min-h-[200px] md:min-h-[300px] rounded" />
        </div>
        <div className="md:col-span-4 bg-bg-card p-4 space-y-3">
          <SkeletonBase className="h-4 w-24" />
          <SkeletonBase className="h-10 w-full rounded-lg" />
          <SkeletonBase className="h-10 w-full rounded-lg" />
          <SkeletonBase className="h-10 w-full rounded-lg" />
          <SkeletonBase className="h-10 w-full rounded-lg mt-4" />
        </div>
      </div>
    </div>
  );
}
