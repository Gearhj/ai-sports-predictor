import ReactEChartsCore from 'echarts-for-react/lib/core'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import type { EChartsOption } from 'echarts'

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, SVGRenderer])

export const chartTheme = {
  text: '#26374d',
  muted: '#76869b',
  grid: '#e5ebf1',
  teal: '#087f7a',
  navy: '#183b65',
  cyan: '#2f91c9',
  orange: '#d97332',
  red: '#bd4d53',
  background: 'transparent',
}

export function AnalyticsChart({
  option,
  height = 300,
  ariaLabel,
}: {
  option: EChartsOption
  height?: number
  ariaLabel: string
}) {
  return (
    <div className="chart" role="img" aria-label={ariaLabel}>
      <ReactEChartsCore
        echarts={echarts}
        option={{
          animationDuration: 500,
          textStyle: { fontFamily: 'Inter, Segoe UI, sans-serif', color: chartTheme.text },
          ...option,
        }}
        opts={{ renderer: 'svg' }}
        notMerge
        lazyUpdate
        style={{ height, width: '100%' }}
      />
    </div>
  )
}
