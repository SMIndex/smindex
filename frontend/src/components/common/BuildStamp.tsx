import { BUILD_COMMIT, BUILD_TIME, buildLabel } from '@/lib/buildInfo';

/** The build this page is running, in the footer of every page. */
export default function BuildStamp({ className }: { className?: string }) {
  return (
    <span
      className={className}
      data-build-commit={BUILD_COMMIT}
      data-build-time={BUILD_TIME}
      title={`Build ${BUILD_COMMIT}, compiled ${BUILD_TIME}`}
    >
      build {buildLabel()}
    </span>
  );
}
