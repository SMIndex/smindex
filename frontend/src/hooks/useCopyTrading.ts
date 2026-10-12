import { useEffect, useCallback, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useWebSocket } from '@/hooks/useWebSocket';
import { getMyFollows, postFollow, deleteFollow, patchFollow } from '@/lib/api';
import { useAuth } from '@/hooks/useAuth';
import type { CopyExecution } from '@/types/trade';

export function useCopyTrading() {
  const queryClient = useQueryClient();
  const { subscribe, unsubscribe } = useWebSocket();
  const { isAuthenticated } = useAuth();
  const [recentExecutions, setRecentExecutions] = useState<CopyExecution[]>([]);

  const {
    data: follows,
    isLoading,
    refetch: refetchFollows,
  } = useQuery({
    queryKey: ['my-follows'],
    queryFn: getMyFollows,
    enabled: isAuthenticated,
  });

  const followMutation = useMutation({
    mutationFn: (config: {
      leader_wallet: string;
      allocation_usd: number;
      max_leverage: number;
      auto_copy?: boolean;
    }) => postFollow(config),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['my-follows'] });
      queryClient.invalidateQueries({ queryKey: ['leaders'] });
    },
  });

  const unfollowMutation = useMutation({
    mutationFn: (leaderId: string) => deleteFollow(leaderId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['my-follows'] });
      queryClient.invalidateQueries({ queryKey: ['leaders'] });
    },
  });

  const updateFollowMutation = useMutation({
    mutationFn: ({
      leaderId,
      update,
    }: {
      leaderId: string;
      update: { allocation_usd?: number; max_leverage?: number; is_active?: boolean };
    }) => patchFollow(leaderId, update),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['my-follows'] });
    },
  });

  const handleCopyUpdate = useCallback((event: Event) => {
    const execution = (event as CustomEvent).detail as CopyExecution;
    if (execution) {
      setRecentExecutions((prev) => [execution, ...prev].slice(0, 50));
    }
  }, []);

  useEffect(() => {
    if (isAuthenticated) {
      subscribe('copy_updates');
      window.addEventListener('copy_update', handleCopyUpdate);
      return () => {
        unsubscribe('copy_updates');
        window.removeEventListener('copy_update', handleCopyUpdate);
      };
    }
  }, [isAuthenticated, subscribe, unsubscribe, handleCopyUpdate]);

  return {
    follows: follows ?? [],
    isLoading,
    refetchFollows,
    follow: followMutation.mutateAsync,
    isFollowing: followMutation.isPending,
    unfollow: unfollowMutation.mutateAsync,
    isUnfollowing: unfollowMutation.isPending,
    updateFollow: updateFollowMutation.mutateAsync,
    isUpdating: updateFollowMutation.isPending,
    recentExecutions,
  };
}
