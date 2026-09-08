// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

import { InstallReviewDialog } from './install-review-dialog'
import { RemoveReviewDialog } from './remove-review-dialog'
import { TrustReviewDialog } from './trust-review-dialog'

afterEach(cleanup)

const phases = [
  'queued',
  'running',
  'fetching',
  'reviewing',
  'validating',
  'committing',
  'recovering',
  'completed',
  'failed',
  'cancelled',
  'checking'
] as const

const locales = [
  [
    'ar',
    [
      'قيد الانتظار',
      'قيد التنفيذ',
      'جارٍ الجلب',
      'جارٍ المراجعة',
      'جارٍ التحقق',
      'جارٍ تثبيت التغييرات',
      'جارٍ الاسترداد',
      'مكتمل',
      'فشل',
      'أُلغي',
      'جارٍ البحث عن تحديثات'
    ]
  ],
  [
    'ja',
    [
      '待機中',
      '実行中',
      '取得中',
      '確認中',
      '検証中',
      '変更を確定中',
      '復旧中',
      '完了',
      '失敗',
      'キャンセル済み',
      '更新を確認中'
    ]
  ],
  [
    'zh',
    [
      '排队中',
      '运行中',
      '获取中',
      '审查中',
      '验证中',
      '提交更改中',
      '恢复中',
      '已完成',
      '已失败',
      '已取消',
      '检查更新中'
    ]
  ],
  [
    'zh-hant',
    [
      '佇列中',
      '執行中',
      '取得中',
      '審查中',
      '驗證中',
      '提交變更中',
      '復原中',
      '已完成',
      '已失敗',
      '已取消',
      '檢查更新中'
    ]
  ]
] as const

describe.each(locales)('controlled lifecycle progress in %s', (locale, labels) => {
  it.each(['install', 'update', 'remove', 'trust'] as const)(
    'translates %s phase labels without changing backend progress',
    mode => {
      for (const [index, phase] of phases.entries()) {
        const props = { onCancelOperation: vi.fn(), onClose: vi.fn(), onPrepareAgain: vi.fn(), open: true }
        const view = { kind: 'progress' as const, phase, progress: 40, cancellable: false }
        render(
          <I18nProvider configClient={null} initialLocale={locale}>
            {mode === 'remove' ? (
              <RemoveReviewDialog {...props} onConfirm={vi.fn()} sourceName="company" view={view} />
            ) : mode === 'trust' ? (
              <TrustReviewDialog
                {...props}
                onGrant={vi.fn()}
                onSelectionChange={vi.fn()}
                selection={{ type: 'all' }}
                view={view}
              />
            ) : (
              <InstallReviewDialog {...props} onConfirm={vi.fn()} onReviewTrust={vi.fn()} view={{ ...view, mode }} />
            )}
          </I18nProvider>
        )
        const progress = screen.getByRole('status')
        expect(progress.textContent).toContain(labels[index])
        expect(progress.textContent).toContain('40%')

        for (const controlled of phases) {
          expect(progress.textContent).not.toContain(controlled)
        }
        cleanup()
      }
    }
  )
})
