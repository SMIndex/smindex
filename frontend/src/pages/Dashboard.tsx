import DashboardPage from '@/components/dashboard/DashboardPage';
import ErrorBoundary from '@/components/common/ErrorBoundary';

export default function Dashboard() {
  return (
    <ErrorBoundary>
      <DashboardPage />
    </ErrorBoundary>
  );
}
