import CopyTradePage from '@/components/copytrade/CopyTradePage';
import ErrorBoundary from '@/components/common/ErrorBoundary';

export default function CopyTrade() {
  return (
    <ErrorBoundary>
      <CopyTradePage />
    </ErrorBoundary>
  );
}
