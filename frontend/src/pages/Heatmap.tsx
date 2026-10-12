import HeatmapPage from '@/components/heatmap/HeatmapPage';
import ErrorBoundary from '@/components/common/ErrorBoundary';

export default function Heatmap() {
  return (
    <ErrorBoundary>
      <HeatmapPage />
    </ErrorBoundary>
  );
}
