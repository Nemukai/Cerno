import * as echarts from "echarts/core";
import {
  BarChart,
  BoxplotChart,
  GraphChart,
  HeatmapChart,
  LineChart,
  PieChart,
  SankeyChart,
  ScatterChart,
} from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  BarChart,
  BoxplotChart,
  GraphChart,
  HeatmapChart,
  LineChart,
  PieChart,
  SankeyChart,
  ScatterChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  VisualMapComponent,
  CanvasRenderer,
]);

export { echarts };

export const EMBER = "#E85D23";
