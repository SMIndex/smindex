import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getLeaders } from '@/lib/api';

export type LeaderboardTab = 'all' | 'day' | 'active';

export function useLeaderboard() {
  const [tab, setTab] = useState<LeaderboardTab>('all');
  const [sortBy, setSortBy] = useState<'pnl' | 'vol'>('pnl');
  const [page, setPage] = useState(0);
  const limit = 20;

  const { data: leaders, isLoading } = useQuery({
    queryKey: ['leaders', tab, sortBy, page],
    queryFn: () =>
      getLeaders({
        sort: sortBy,
        period: tab === 'active' ? 'day' : tab,
        active_only: tab === 'active' ? true : undefined,
        skip: page * limit,
        limit,
      }),
  });

  const toggleSort = (field: string) => {
    const mapped = field === 'volume' ? 'vol' : 'pnl';
    if (sortBy === mapped) return;
    setSortBy(mapped as 'pnl' | 'vol');
    setPage(0);
  };

  const switchTab = (newTab: LeaderboardTab) => {
    setTab(newTab);
    setPage(0);
  };

  return {
    leaders: leaders ?? [],
    isLoading,
    sortBy,
    tab,
    switchTab,
    toggleSort,
    page,
    setPage,
    limit,
    hasMore: (leaders?.length ?? 0) === limit,
  };
}
