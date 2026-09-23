import { useState } from 'react';
import { clsx } from 'clsx';
import { useCopyTrading } from '@/hooks/useCopyTrading';
import { useToast } from '@/components/common/Toast';
import Modal from '@/components/common/Modal';

interface EditFollowModalProps {
  follow: {
    leader_wallet: string;
    allocation_usd: number;
    max_leverage: number;
    is_active: boolean;
  };
  onClose: () => void;
}

export default function EditFollowModal({ follow, onClose }: EditFollowModalProps) {
  const [allocationUsd, setAllocationUsd] = useState(follow.allocation_usd);
  const [maxLeverage, setMaxLeverage] = useState(follow.max_leverage);
  const [isActive, setIsActive] = useState(follow.is_active);
  const { updateFollow, isUpdating } = useCopyTrading();
  const toast = useToast();

  const handleSave = async () => {
    try {
      await updateFollow({
        leaderId: follow.leader_wallet,
        update: {
          allocation_usd: allocationUsd,
          max_leverage: maxLeverage,
          is_active: isActive,
        },
      });
      toast.success('Follow config updated');
      onClose();
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed to update');
    }
  };

  return (
    <Modal isOpen={true} onClose={onClose} title="Edit Follow">
      <div className="space-y-5">
        <div>
          <label className="block text-sm font-medium text-text-primary mb-1.5">
            Allocation (USD)
          </label>
          <input
            type="number"
            value={allocationUsd}
            onChange={(e) => setAllocationUsd(Number(e.target.value))}
            min={10}
            max={100000}
            step={10}
            className="input-field"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-text-primary mb-1.5">
            Max Leverage: {maxLeverage}x
          </label>
          <input
            type="range"
            value={maxLeverage}
            onChange={(e) => setMaxLeverage(Number(e.target.value))}
            min={1}
            max={100}
            step={1}
            className="w-full h-2 bg-bg-secondary rounded-lg appearance-none cursor-pointer accent-accent"
          />
        </div>

        <div className="flex items-center justify-between">
          <span className="text-sm text-text-primary">Active</span>
          <button
            onClick={() => setIsActive(!isActive)}
            className={clsx(
              'w-11 h-6 rounded-full transition-colors relative',
              isActive ? 'bg-success' : 'bg-bg-secondary',
            )}
          >
            <div
              className={clsx(
                'w-4 h-4 rounded-full bg-white absolute top-1 transition-transform',
                isActive ? 'translate-x-6' : 'translate-x-1',
              )}
            />
          </button>
        </div>

        <div className="flex items-center gap-3">
          <button onClick={onClose} className="btn-secondary flex-1">
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={isUpdating}
            className="btn-primary flex-1"
          >
            {isUpdating ? 'Saving...' : 'Save Changes'}
          </button>
        </div>
      </div>
    </Modal>
  );
}
