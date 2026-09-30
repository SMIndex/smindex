import { useParams, Navigate } from 'react-router-dom';
import LeaderProfile from '@/components/copytrade/LeaderProfile';
import ErrorBoundary from '@/components/common/ErrorBoundary';

export default function LeaderDetail() {
  const { leaderId } = useParams<{ leaderId: string }>();

  if (!leaderId) {
    return <Navigate to="/copy" replace />;
  }

  return (
    <ErrorBoundary>
      <LeaderProfile leaderId={leaderId} />
    </ErrorBoundary>
  );
}
