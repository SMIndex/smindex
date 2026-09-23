import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import api from '@/lib/api';
import { useAuth } from '@/hooks/useAuth';
import { shortenAddress, formatTimeAgo } from '@/lib/formatters';
import { MARKETS } from '@/config/constants';
import LoadingSpinner from '@/components/common/LoadingSpinner';

function CreatePost({ onCreated }: { onCreated: () => void }) {
  const [content, setContent] = useState('');
  const [marketId, setMarketId] = useState<number | ''>('');
  const { login, isAuthenticated } = useAuth();

  const mutation = useMutation({
    mutationFn: (data: any) => api.post('/api/social/posts', data).then((r) => r.data),
    onSuccess: () => { setContent(''); onCreated(); },
  });

  const handleSubmit = async () => {
    if (!isAuthenticated) { await login(); return; }
    if (!content.trim()) return;
    mutation.mutate({
      content: content.trim(),
      market_id: marketId || null,
    });
  };

  return (
    <div className="card space-y-3">
      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value.slice(0, 500))}
        placeholder="Share your trade analysis..."
        rows={3}
        className="input-field text-sm resize-none"
      />
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <select
            value={marketId}
            onChange={(e) => setMarketId(e.target.value ? Number(e.target.value) : '')}
            className="input-field text-xs w-24"
          >
            <option value="">Market</option>
            {Object.entries(MARKETS).map(([id, m]) => (
              <option key={id} value={id}>{m.symbol}</option>
            ))}
          </select>
          <span className="text-[10px] text-text-secondary">{content.length}/500</span>
        </div>
        <button
          onClick={handleSubmit}
          disabled={mutation.isPending || !content.trim()}
          className="btn-primary text-xs px-4"
        >
          {mutation.isPending ? 'Posting...' : 'Post'}
        </button>
      </div>
      {mutation.error && <div className="text-xs text-danger">{(mutation.error as any)?.response?.data?.detail || 'Failed to post'}</div>}
    </div>
  );
}

function PostCard({ post, onRefresh }: { post: any; onRefresh: () => void }) {
  const [showComments, setShowComments] = useState(false);
  const [comment, setComment] = useState('');
  const { isAuthenticated } = useAuth();

  const { data: comments } = useQuery({
    queryKey: ['comments', post.id],
    queryFn: () => api.get(`/api/social/posts/${post.id}/comments`).then((r) => r.data),
    enabled: showComments,
  });

  const likeMutation = useMutation({
    mutationFn: () =>
      post.liked_by_me
        ? api.delete(`/api/social/posts/${post.id}/like`)
        : api.post(`/api/social/posts/${post.id}/like`),
    onSuccess: onRefresh,
  });

  const commentMutation = useMutation({
    mutationFn: (content: string) => api.post(`/api/social/posts/${post.id}/comment`, { content }),
    onSuccess: () => { setComment(''); onRefresh(); },
  });

  return (
    <div className="card space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-full bg-accent/10 flex items-center justify-center">
            <span className="text-xs font-bold text-accent">{post.wallet_address?.[2]?.toUpperCase() || '?'}</span>
          </div>
          <div>
            <div className="text-sm font-medium text-text-primary font-mono">{shortenAddress(post.wallet_address || '')}</div>
            <div className="text-[10px] text-text-secondary">{formatTimeAgo(post.created_at)}</div>
          </div>
        </div>
        {post.market_id && MARKETS[post.market_id] && (
          <span className="text-[10px] px-2 py-0.5 bg-accent/10 text-accent rounded font-medium">{MARKETS[post.market_id].symbol}</span>
        )}
      </div>

      {/* Content */}
      <p className="text-sm text-text-primary whitespace-pre-wrap break-words">{post.content}</p>

      {/* Position snapshot */}
      {post.position_snapshot && (
        <div className="p-2 bg-bg-secondary rounded text-[10px] text-text-secondary">
          Position snapshot attached
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center gap-4 text-xs text-text-secondary">
        <button
          onClick={() => isAuthenticated && !likeMutation.isPending && likeMutation.mutate()}
          className={clsx("flex items-center gap-1 transition-colors", post.liked_by_me ? "text-red-500" : "hover:text-accent")}
        >
          <svg className="w-4 h-4" fill={post.liked_by_me ? "currentColor" : "none"} stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z" />
          </svg>
          {post.likes_count || 0}
        </button>
        <button
          onClick={() => setShowComments(!showComments)}
          className="flex items-center gap-1 hover:text-accent transition-colors"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
          </svg>
          {post.comments_count || 0}
        </button>
      </div>

      {/* Comments */}
      {showComments && (
        <div className="space-y-2 pt-2 border-t border-text-secondary/10">
          {comments?.map((c: any) => (
            <div key={c.id} className="flex gap-2 text-xs flex-wrap">
              <span className="font-mono text-accent shrink-0">{shortenAddress(c.wallet_address || '')}</span>
              <span className="text-text-primary break-words min-w-0 flex-1">{c.content}</span>
              <span className="text-text-secondary/50 shrink-0">{formatTimeAgo(c.created_at)}</span>
            </div>
          ))}
          {isAuthenticated && (
            <div className="flex gap-2">
              <input
                value={comment}
                onChange={(e) => setComment(e.target.value.slice(0, 200))}
                placeholder="Add a comment..."
                className="input-field text-xs flex-1"
                onKeyDown={(e) => e.key === 'Enter' && comment.trim() && commentMutation.mutate(comment.trim())}
              />
              <button
                onClick={() => comment.trim() && commentMutation.mutate(comment.trim())}
                disabled={!comment.trim()}
                className="btn-primary text-[10px] px-3"
              >
                Send
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function SocialPage() {
  const queryClient = useQueryClient();
  const { data: posts, isLoading } = useQuery({
    queryKey: ['social-feed'],
    queryFn: () => api.get('/api/social/feed?limit=30').then((r) => r.data),
    refetchInterval: 15000,
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['social-feed'] });

  // Real-time feed updates via WS
  useEffect(() => {
    const handler = () => refresh();
    window.addEventListener('social_feed', handler);
    return () => window.removeEventListener('social_feed', handler);
  }, []);

  return (
    <div className="max-w-2xl mx-auto space-y-4">
      <div>
        <h1 className="text-xl font-bold text-text-primary">Social Feed</h1>
        <p className="text-xs text-text-secondary">Share trade ideas and analysis with the community</p>
      </div>

      <CreatePost onCreated={refresh} />

      {isLoading ? (
        <div className="flex justify-center py-8"><LoadingSpinner /></div>
      ) : posts?.length === 0 ? (
        <div className="card text-center py-8 text-sm text-text-secondary">
          No posts yet. Be the first to share!
        </div>
      ) : (
        <div className="space-y-3">
          {posts?.map((post: any) => (
            <PostCard key={post.id} post={post} onRefresh={refresh} />
          ))}
        </div>
      )}
    </div>
  );
}
