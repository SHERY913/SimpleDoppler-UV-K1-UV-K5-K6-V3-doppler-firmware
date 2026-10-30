# 参考-Look4Sat 罗盘 / 天空图实现（只读逆向规格）

目标样本：`C:\Users\13766\Downloads\look4sat.apk`
包名 **`com.rtbishop.look4sat`**（注意：实际清单里带 `com.` 前缀，任务描述里的 `rtbishop.look4sat` 是显示名）
versionName 4.4.9 / versionCode 449，8.0 MB，4690 个 DEX 类

工具链：python + androguard 4.1.4（`AnalyzeAPK` + `DecompilerDAD`），无 jadx/apktool/Android SDK。
样本被 **R8 全量混淆 + repackage**：除 `MainActivity / MainApplication / Look4SatDb / Look4SatDb_Impl` 外，连 androidx/compose 的类名都被改成 `La1b; Lyl; Lk70; Lti0;` 这种，所以一切"按 androidx 类名 grep"都失效，只能靠常量与调用图定位。下文类名均为混淆后的真实 DEX 类名。

---

## 一、结论速览（该抄什么）

1. **姿态只用一个传感器**：`TYPE_ROTATION_VECTOR`（`getDefaultSensor(11)`），`registerListener(this, sensor, 16000)` → 16000 µs ≈ 62.5 Hz（不是 `SENSOR_DELAY_*` 常量，是裸数字）。
2. 姿态链：`getRotationMatrixFromVector` → `remapCoordinateSystem`（按 `Display.getRotation()` 做 0/90/180/270 remap）→ `getOrientation`，只取 `[0]=azimuth`、`[1]=pitch`。
3. **方位角**：`az = (deg(orient[0]) + 360) % 360`，单位度，0° = 磁北，顺时针。**传感器类内不做真北修正**。
4. **磁偏角在 ViewModel 的流映射里加**：`GeomagneticField(lat, lon, alt, now).getDeclination()`，最终 `azUI = azSensor + declination + radarCompassOffset`、`pitchUI = pitch + radarCompassOffsetElev`（后两者是设置页可调的校准偏移）。
5. **平滑 = 一阶低通 α = 0.15f**，先把增量折到 (−180,180] 走短路径，再 `prev + 0.15*delta`；pitch 同样 α 但**不做** wrap。最后 `rint(x*10)/10` 量化到 0.1° 后塞进 `StateFlow`。
6. **天空图是等距投影（zenith 线性）**：`r = R * (π/2 − el) / (π/2)`，`x = r·sin(az)`，`y = −r·cos(az)`（屏幕 y 向下 ⇒ 0° 在正上方）。`R = min(w,h)/2 * 0.95`。
7. 圆环 3 圈（`R, 2R/3, R/3`）+ 横竖十字线，线宽 6 px；环上标 `90°/60°/30°`（**天顶角**，不是仰角）。**全图没有 N/E/S/W 文字**，朝向靠整张画布 `rotate(-azimuth)`。
8. 当前卫星：实心圆 r=16 px + 呼吸环 16→64 px（alpha 1→0，1000 ms 无限循环）；月亮十字（±36 px 臂 + r=18 圆），太阳/月亮图标 52×52。
9. **手机指向标记**：十字 + r=18 圆，位置 = `project(azimuth, max(0, −pitch))`，无"对准/命中"阈值判定，靠叠加视觉对齐。
10. 生命周期：只有"设置开关 + `onCleared`"两处注册/注销，无 `onPause`/息屏处理，无 `getDefaultSensor()==null` 降级 —— 这三点都别抄（见第五节）。

---

## 二、类与方法清单（混淆类名 → 职责 → 落盘路径）

落盘目录：`C:\Users\13766\Documents\deepseek-harness\default-workspace\多普勒\reverse\look4sat-decompiled\`

| 类全名（DEX） | 职责 | 方法 | 落盘文件 |
|---|---|---|---|
| `Lqg2;` | 罗盘传感器封装（`SensorEventListener`） | `.onSensorChanged` `.a(GeomagneticField)` `<init>` | `qg2.java`（106 行） |
| `Lo22;` | Radar ViewModel（状态机、天体、注册/注销） | `.d()`（清理） `.j(...)`（状态更新） | `o22.java`（823 行） |
| `Lj22;` | 设置/位置/传感器事件 → 状态的映射 lambda | `.a(dj0,pw)` `.m(Object,pw)` | `j22.java`（154 行） |
| `Lg22;` | 注册传感器的协程体 | `.h(Object,Object)`（case 6 内） | `g22.java` |
| `Lh22;` | 雷达屏 UI 状态 data class（16 字段） | `.toString()` 给出全部字段名 | `h22.java` |
| `Lp01;` | 天空图绘制工具集（投影、圆环、指针、图标） | `.H(DDD)J` `.t(k70,F,J)` `.s(k70,F,J,qy2)` `.q(...)` `.r(...)` `.h(...)`（Composable） | `p01.java`（2490 行） |
| `Li22;` | 天空图 `DrawScope` lambda（所有 draw 调用都在这里） | `.i(Object)` | `i22.java`（351 行） |
| `Lc22;` | Radar 屏 Composable lambda（调用 `p01.h`） | `.g(Object,Object,Object)` | `c22.java` |
| `Lio1;` | 当前卫星位置（a=azimuth, b=elevation, g=?, l/m 布尔） | `.a(J)J` `.b(J)J` | `io1.java` |
| `Lfj2;` | SharedPreferences → 设置对象（给出 prefs key） | `<init>` | `fj2.java` |
| `Llo1;` | 设置 data class（`l/m` = 罗盘 az/el 校准偏移） | `.a(...)` copy | `lo1.java` |
| `Lt91;` | 依赖注入工厂（构造 `o22` + `qg2`） | `case 2` | `t91.java` |

### 关键代码原文（去混淆后的可读形式，行号即落盘文件行号）

`qg2.java:20` 只取旋转向量传感器：

```java
this.d = p2.getDefaultSensor(11);
```

`qg2.java:48-81` 姿态矩阵 + 屏幕旋转 remap + 欧拉角：

```java
if (p8.sensor.getType() == 11) {
    SensorManager.getRotationMatrixFromVector(v0_4, p8.values);      // v0_4 = this.e, float[9]
    ...
    if (v1_2 == 1)      v1_3 = SensorManager.remapCoordinateSystem(v0_4, 2, 129, v5_0);    // AXIS_Y, AXIS_MINUS_X
    else if (v1_2 == 2) v1_3 = SensorManager.remapCoordinateSystem(v0_4, 129, 130, v5_0);  // AXIS_MINUS_X, AXIS_MINUS_Y
    else if (v1_2 == 3) v1_3 = SensorManager.remapCoordinateSystem(v0_4, 130, 1, v5_0);    // AXIS_MINUS_Y, AXIS_X
    else                v1_3 = 0;                                    // rotation==0 不 remap
    if (v1_3 != 0) System.arraycopy(v5_0, 0, v0_4, 0, 9);
    SensorManager.getOrientation(v0_4, v1_5);                        // v1_5 = this.g, float[3]
```

`qg2.java:82-102` 角度、低通、量化：

```java
float v8_4 = ((((float)(((double) v1_5[0]) * 57.29577951308232 /*4633260481411531256*/)) + 360.0f) % 360.0f);
float v1_8 = ((float)(((double) v1_5[1]) * 57.29577951308232));
if (this.j) {                                   // 非首帧
    float v2_1 = this.h;
    float v8_5 = (v8_4 - v2_1);
    while (v8_5 >  180.0f)  v8_5 -= 360.0f;
    while (v8_5 <= -180.0f) v8_5 += 360.0f;
    v8_4 = ((((v8_5 * 0.15f) + v2_1) + 0f) % 360.0f);   // α=0.15 低通，再做一次 360 取模
    this.h = v8_4;
    float v0_3 = this.i;
    this.i = (((v1_8 - v0_3) * 0.15f) + v0_3);          // pitch 同 α，无 wrap
} else { this.h = v8_4; this.i = v1_8; this.j = true; } // 首帧直接落值
this.c.k(0, new qq1(Math.rint(v8_4 * 10.0f) / 10.0f, Math.rint(this.i * 10.0f) / 10.0f));
```

`qg2.java:31-37` 磁偏角（独立静态方法，传感器回调里**没有**调用它）：

```java
return new android.hardware.GeomagneticField((float) p7.a, (float) p7.b, (float) p7.c,
        System.currentTimeMillis()).getDeclination();
```

`j22.java:130-137` 真北 + 校准偏移在这里加（这才是唯一的修正点）：

```java
case 3:
    int v11_4 = new qq1(
        ((qq1) p27).g.floatValue() + v4_4.m + v4_4.n,   // azSensor + declination + radarCompassOffset
        ((qq1) p27).h.floatValue() + v4_4.o);           // pitch    + radarCompassOffsetElev
    ...
    h22.a(state, ..., 0, 0, v11_4 /* orientationValues */, ...);   // mask 65519 只替换 e
```

`p01.java:224-229` 天空图投影（全文档最重要的一段）：

```java
public static final long H(double p2 /*azDeg*/, double p4 /*elDeg*/, double p6 /*radiusPx*/)
{
    long v4_5 = (((4609753056924675352 /*π/2*/ - p4) * p6) / 4609753056924675352);  // r = R*(90-el)/90
    double v0_1 = (4609753056924675352 - p2);                                        // θ = π/2 - az
    return ((((long) Float.floatToRawIntBits((-((float)(Math.sin(v0_1) * v4_5))))) & 4294967295)
          | (((long) Float.floatToRawIntBits(((float)(Math.cos(v0_1) * v4_5)))) << 32));
}
```

`p01.java:2348-2356` 三条圆环 + 横竖十字线（半径 R、2R/3、R/3，线宽 6 px）：

```java
float v1_0 = 0;
while (v1_0 < 3) {
    k70.F(p21, p23, (p22 - (((float) v1_0) * (p22 / 3.0f))), 0, new as2(6.0f,0,0,0,0,30), 108); // drawCircle, Stroke
    v1_0++;
}
// 水平线 (cx-R,cy)-(cx+R,cy)，竖直线 (cx,cy-R)-(cx,cy+R)，同样 6.0f / style 108
```

`p01.java:2238-2247` 环标签（天顶角）：

```java
cl v1_1 = (p35 / 3.0f);                       // p35 = R
...
int v6_20 = ... (cx, p35 - (v15 * v1_1) - 32.0f);   // 环正上方内侧 32px
qd1 v3_9 = new gd(fl1.f(((3 - v15) * 30), " ", "°")); // "90°" / "60°" / "30°"
```

`i22.java:71` 半径 = `min(w,h)/2 * 0.95`：

```java
double v14_3 = ((bm2.b(((k70) p48).d()) / 2.0f) * 0.949999988079071f);
```

`i22.java:166-187` 整张图按手机方位角反向旋转：

```java
float v2_0 = v0_0.i;                       // shouldUseCompass
ff1   v3_3 = v0_0.j;                       // orientationValues(qq1)
float v6_0 = (v2_0 == 0) ? 0 : (-((Number) v3_3.g).floatValue());   // -azimuth
if (v0_0.k) v6_0 += 180.0f;                // shouldFlipRadar
((v53) v9_3.g).x(v6_0, v7_4);              // canvas.rotate(deg, cx, cy)
```

`i22.java:197-198` 圆环 + 标签、`213/218` 轨迹两遍、`233-243` 卫星点 + 呼吸环、`249/256` 太阳月亮：

```java
p01.t(canvas, R, ringColor);                       // 3 环 + 十字
p01.s(canvas, R, ringColor, textMeasurer);         // 90/60/30 标签
k70.E0(canvas, trackPath, color, 0, Brush/stroke 6.0f..., 52);      // 实线 6px
k70.E0(canvas, trackPath, color, 0, brush(pathDashEffect), 52);     // 加一遍虚线（见下）
if (orbitalPos.b > 0) { ... k70.F(canvas, color, 16.0f, p01.H(az,el,R), 0, 120);  // r=16 实心
                        if (!orbitalPos.l) k70.F(canvas, alphaFade, anim, offset, 0, 120); }  // 呼吸环
```

`p01.java:2205-2216` 手机指向标记（十字 r=18 + ±36 臂）：

```java
as2 v0_2 = (((double) p38) * 0.017453292519943295);
if (v0_2 > 0) v0_2 = 0;                       // 仰角 >0 一律压到地平面
long v14 = p01.H(p37 * 0.017453292519943295, (- v0_2), p39);   // 即 el = max(0, -pitch)
// ±36px 横线 / 竖线 + drawCircle(radius = 18.0f, stroke 6.0f)
```

`p01.java:1810-1811` 两个无限动画（Compose 原生 API，label 名保留）：

```java
boolean v4_1 = y51.h(y51.T("animScale", p46, 0), 16.0f, 64.0f,
                     ez.z(ez.S(1000, 6, 0), 0, 6), "animScale", p46, 29112, 0);
qq1     v5_0 = y51.h(y51.T("sweep",     p46, 0), 0f,   360.0f,
                     ez.z(ez.S(8000, 2, d80.b), 0, 6), "sweepDegrees", p46, 29112, 0);
```

`g22.java:197` 注册采样率；`o22.java:127-137` 注销：

```java
v0_17.a.registerListener(v0_17, v9_7, 16000);          // 16000 µs
...
this.t.g(0);                                            // Job.cancel(null)
qg2 v2_1 = this.f; v2_1.a.unregisterListener(v2_1);     // unregister
```

---

## 三、八个问题逐条答案（含证据）

### Q1 怎么拿姿态
- **传感器**：`TYPE_ROTATION_VECTOR`，证据 `qg2.java:20` `p2.getDefaultSensor(11)`。全 APK 没有 `TYPE_ACCELEROMETER`+`TYPE_MAGNETIC_FIELD` 自算，也没有已废弃的 `Sensor.TYPE_ORIENTATION`。
- **采样率**：`g22.java:197` `registerListener(listener, sensor, 16000)` → 16000 µs ≈ 62.5 Hz。没有用 `SENSOR_DELAY_GAME(20000)` / `SENSOR_DELAY_FASTEST(0)`。
- **矩阵**：`SensorManager.getRotationMatrixFromVector(float[9], event.values)` → `SensorManager.getOrientation(float[9], float[3])`，没有自写四元数。
- **屏幕旋转**：`DisplayManager.getDisplay(0).getRotation()`，分 1/2/3 三个分支调 `remapCoordinateSystem`：
  - `1`（90°）→ `(AXIS_Y=2, AXIS_MINUS_X=129)`
  - `2`（180°）→ `(AXIS_MINUS_X=129, AXIS_MINUS_Y=130)`
  - `3`（270°）→ `(AXIS_MINUS_Y=130, AXIS_X=1)`
  - `0` 不 remap；remap 成功才 `arraycopy` 回原矩阵。**没有** `ROTATION_90/`常量的可读名，全是裸 int（2/129/130/1）。
- 监听器是单实例、`SensorEventListener` 直接实现（`Lqg2 implements android.hardware.SensorEventListener`），回调里先 `if (event.sensor.getType() == 11)` 过滤。

### Q2 方位角计算
- 公式：`azDeg = ( (orient[0] * 180/π) + 360 ) % 360`（`qg2.java:82`，常量 `4633260481411531256 = 57.29577951308232 = 180/π`，`1135869952 = 360.0f`）。
- 单位 **度**，0° = **磁北**，顺时针增大；值域 `[0, 360)`。
- **是否含磁偏角**：`qg2` 内不含；它只提供 `qg2.a(pos)` 静态方法算 `GeomagneticField(...,).getDeclination()`（`qg2.java:31-37`）。真正相加在 `j22.java:133`：`azUI = az + declination + radarCompassOffset`。所以 **UI 显示的是真北 + 用户校准偏移**，但传感器流本身是磁北。
- 磁北/真北：内部两种量都存（`o22.m` = declination，`lo1.l` = `radarCompassOffset`，`lo1.m` = `radarCompassOffsetElev`，见 `fj2.java:233` 的 prefs key）。
- **倾斜补偿**：由 `getRotationMatrixFromVector` + `getOrientation` 天然完成（旋转向量本身就是重力对齐的姿态），没有额外用加速度计做 tilt compensation；但 pitch（`orient[1]`）参与了"手机指向"标记的计算（见 Q6），也就是 **俯仰参与**、横滚**完全不参与**（`orient[2]` 从未被读取）。

### Q3 平滑 / 滤波
- 一阶低通（EMA），**α = 0.15f**（`1041865114 = 0.15000000596046448`），首帧不滤波直接落值并置标志 `j=true`。
- 方位角增量先做**最短路径折叠**：`while(delta > 180) delta -= 360; while(delta <= -180) delta += 360;` 然后 `prev + 0.15*delta`，再 `% 360`。
- pitch 用**同一个 α**，但**没有** wrap 折返（pitch 值域小，问题不大）。
- 输出量化：`Math.rint(x * 10.0f) / 10.0f` → **0.1° 分辨率**，写进 `MutableStateFlow`（`ar2`）。
- **解耦方式**：传感器回调只更新 `StateFlow`（`.k(0, qq1)`），Compose 侧 `collectAsState` 驱动重组；没有自己做定时器/攒帧，UI 刷新频率 = Compose 帧率，传感器频率 ≈ 62.5 Hz。注意回调里**没有任何节流**，60 Hz 状态更新会一直触发重组（见第五节）。

### Q4 天空图投影公式（像素级）
唯一投影函数 `p01.H(azDeg, elDeg, R)`：

```
r  = R * (90° − el) / 90°            // 等距 / zenith 线性投影（不是等面积，不是 stereographic）
x  = r * sin(az)
y  = −r * cos(az)                    // 屏幕 y 向下 ⇒ az=0 向上（正北），az=90 向右（正东）
```

- 输入单位：**度**（调用方自己乘 `0.017453292519943295` 转弧度传进去的只有 `p01.q/r`，`p01.H` 自身收的是弧度，见下）。
  ⚠️ 精确说明：`H` 的参数其实是**弧度**；`H` 内部用 `π/2 − el` 和 `π/2 − az` 计算，因为 `π/2 = 90°` 的弧度表示。所以 `r = R·(π/2 − el_rad)/(π/2)`。
- 返回：**Compose `Offset` 打包成 long**（高 32 位 = x 的 float 位，低 32 位 = y 的 float 位），画布坐标系为 `drawscope.size` 左上角原点，未加中心偏移 —— 圆心平移在 `i22.java:199-201` 完成：

```java
float cx = Float.intBitsToFloat((int)(canvas.d0() >> 32));    // size.width 的一半（Compose 已把中心放进 d0()）
float cy = Float.intBitsToFloat((int)(canvas.d0() & 4294967295));
((v53) canvas.E().g).B(cx, cy);                                // = translate(cx, cy)
```

即：先用 `Size.center` 拿到圆心，`translate` 到圆心后再调用 `H()` 返回的 `(x, y)` 直接当相对坐标用。
- `R = min(size.width, size.height) / 2 * 0.95`（`i22.java:71`，`0.949999988079071f`）。
- 圆环：**3 圈**，半径 `R`、`R·2/3`、`R·1/3`（`p01.java:2352` `p22 - i*(p22/3.0f)`），线宽 `6.0f` px，`Stroke`（style 108）。
- 十字线：水平 `(cx−R, cy)→(cx+R, cy)`、垂直 `(cx, cy−R)→(cx, cy+R)`，同 6 px。
- 刻度一圈多少度：环上标注是**天顶角 90°/60°/30°**（`p01.java:2247` `fl1.f((3−i)*30, " ", "°")`），也就是对应仰角 0°/30°/60°；**只有 3 个标注，不是每 30° 一个方位刻度**。
- 方位标注：**全 APK 没有 "N"/"E"/"W" 字符串**（字符串全表 `l4s_strings.out` 里独立单字母只有 `"S"`，且不在绘制路径上）。方位语义完全靠"整张画布反向旋转"表达：`canvas.rotate(-azimuth)`，所以 0° 永远画在 12 点钟方向（这是旋转原点）。
- 旋转枢轴：`canvas.rotate(v6_0, w/2, h/2)`（`i22.java:187`，`v7_4 = p48.d0()` 中心）。

### Q5 卫星图标 / 标签 / 颜色语义
- 当前卫星点：`drawCircle(radius = 16.0f px, center = H(az, el, R))`（`i22.java:239`，`1098907648 = 16.0f`）。
- 呼吸环：同一位置，半径 = 动画值（16 → 64 px），颜色 alpha = `1 − anim/64`（`i22.java:241` `np.b(1 - v5_3/64.0f, color)`），即"由内向外扩散并淡出"。
- 太阳 / 月亮：两种画法 —— `p01.r(...)`（`p01.java:2220`）先 `H()` 投影再 `translate(x−0.5, y−0.5)` 后在 1×1 单位盒里画（即**居中补偿 0.5 px 的小图标/圆点**）；`p01.q(...)` 是十字 + r=18 圆（见 Q6）。`io1.l` / `io1.m` 两个布尔控制"是否画呼吸环/是否画外圈"。
- **没有任何防重叠逻辑**：没有标签排布、没有 `Rect` 碰撞剔除、没有引线。理由很明确 —— 雷达屏**只画当前选中那一颗卫星**（`h22.f` = `orbitalPos` 单颗，`h22.g` = `satTrack` 一条轨迹），所以不存在多标签重叠问题。抄的时候如果要做多星显示，防重叠得自己写。
- 颜色：全部作为参数传入（`p01.h` 从主题 `ColorScheme` 取色，`i22` 里是 3~4 个 `long` 颜色：轨迹色 / 环色 / 天体色 / 卫星点色），**未发现硬编码 ARGB 常量**。选中态 = 实心圆 + 呼吸环 + 轨迹画两遍（实线 + 虚线叠加）。

### Q6 手机指向指示（pointer）
- 位置：`p01.q(canvas, azDeg, pitchDeg, R, color)`（`p01.java:2205`），内部 `el = max(0, −pitch)`（`if (el_rad > 0) el_rad = 0; H(az, −el_rad, R)`）。
- 图形：中心实心小圆 `radius=18.0f` + 十字线 `±36.0f` px（`1099956224 = 18.0f`、`1108344832 = 36.0f`、描边 `6.0f`）。
- **没有"对准"阈值**：不做 `|az_sat − az_phone| < X` 判定，不画箭头，不震动。唯一反馈是"整图随手机转 + 一个十字标记"。
- 因为整图已经 `rotate(−azimuth)` 过，所以指向标记在屏幕上的相对位置 = 卫星 az 与手机 az 的**视觉差值**，对齐全靠眼睛看。（`shouldFlipRadar` 为真时整体再 +180°，同时指向标记也跟着转。）

### Q7 动效 / 角度跨 0-360
- 只有两个动画，都是 `rememberInfiniteTransition` + `tween`：
  - `"animScale"`：16 f → 64 f，`tween(1000ms, easing#6)`，`infiniteRepeatable` → 呼吸环。
  - `"sweepDegrees"`：0 f → 360 f，`tween(8000ms, easing#2)` → 雷达扫描线角度。
- **方位角旋转不做插值**：`canvas.rotate(-azimuth)` 直接吃当前值。因此 359.9° → 0.1° 时画布会**瞬间反向整圈**（视觉闪跳）。它只在传感器层用"±180 折叠"保证 `az` 自身平滑，没有对"旋转量"做 unwrap/累加。
- 结论（抄的时候要改）：正确做法是维护一个 `displayAz` 累加量，`displayAz += shortestDelta(targetAz - displayAz)`，再 `animateFloatAsState(displayAz)`，避免跨 0/360 时反向转 360°。

### Q8 权限与生命周期
- Manifest（`AndroidManifest.xml` 实证）：`ACCESS_FINE_LOCATION` + `ACCESS_COARSE_LOCATION`（`GeomagneticField` 与卫星计算必需）+ `INTERNET`/`ACCESS_NETWORK_STATE`/蓝牙/录音（与罗盘无关）。**没有** `<uses-feature android:name="android.hardware.sensor.compass">`，也就是说系统层面不强制要求有磁力计 —— 但代码里不判空。
- 注册/注销点（全部实证）：
  - 注册：`g22.java:197` `registerListener(this, sensor, 16000)`，由设置项 `stateOfSensors`（prefs key，默认 true）触发；`j22.java:116-128` 是开关逻辑：设置关闭 → `job.cancel` + `unregisterListener`；设置打开且 `job == null || !job.isActive()` → 重新 `launch` 注册。
  - 注销：`o22.java:127-137` `d()`（ViewModel 清理路径）里 `Job.cancel` + `unregisterListener(this)`。
- **息屏/后台**：没有任何 `onPause`/`onStop`/`repeatOnLifecycle`/`SCREEN_OFF` 处理，只要设置开着、ViewModel 活着就一直在 62.5 Hz 采集（灭屏后传感器事件会自然降到很低频，等于被动节能，而非主动注销）。
- **失败降级：没有**。`getDefaultSensor(11)` 返回 null 时直接 `registerListener(this, null, 16000)` → 空指针/无回调，UI 永远停在 0°/0°。这是明确的"不该抄"项。

---

## 四、可移植到 Kotlin/Compose 的伪代码（20-40 行）

```kotlin
// 1) 姿态：ROTATION_VECTOR + 屏幕旋转 remap + getOrientation + α=0.15 低通
class Compass(private val sm: SensorManager, private val dm: DisplayManager) : SensorEventListener {
    private val r = FloatArray(9); private val out = FloatArray(9); private val o = FloatArray(3)
    private var az = 0f; private var pitch = 0f; private var primed = false
    private val sensor = sm.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)   // 必须判空！
    private val _state = MutableStateFlow(0f to 0f)                          // az, pitch
    val state: StateFlow<Pair<Float, Float>> = _state
    private val listener = this
    fun start() { sensor?.let { sm.registerListener(listener, it, 16_000) } }   // 16 ms ≈ 62.5 Hz
    fun stop() { sm.unregisterListener(listener) }
    override fun onAccuracyChanged(s: Sensor?, a: Int) {}
    override fun onSensorChanged(e: SensorEvent) {
        if (e.sensor.type != Sensor.TYPE_ROTATION_VECTOR) return
        SensorManager.getRotationMatrixFromVector(r, e.values)
        val rot = dm.getDisplay(Display.DEFAULT_DISPLAY)?.rotation ?: 0
        val ok = when (rot) {                                   // 屏幕旋转补偿
            Surface.ROTATION_90  -> SensorManager.remapCoordinateSystem(r, AXIS_Y, AXIS_MINUS_X, out)
            Surface.ROTATION_180 -> SensorManager.remapCoordinateSystem(r, AXIS_MINUS_X, AXIS_MINUS_Y, out)
            Surface.ROTATION_270 -> SensorManager.remapCoordinateSystem(r, AXIS_MINUS_Y, AXIS_X, out)
            else -> false
        }
        if (ok) System.arraycopy(out, 0, r, 0, 9)
        SensorManager.getOrientation(r, o)
        var target = ((Math.toDegrees(o[0].toDouble()).toFloat() + 360f) % 360f)
        val targetPitch = Math.toDegrees(o[1].toDouble()).toFloat()
        if (!primed) { az = target; pitch = targetPitch; primed = true } else {
            var d = target - az; d = ((d + 540f) % 360f) - 180f          // 折到 (-180,180]
            az = (az + 0.15f * d + 360f) % 360f                          // α = 0.15
            pitch += 0.15f * (targetPitch - pitch)
        }
        _state.value = (round(az * 10f) / 10f) to (round(pitch * 10f) / 10f)   // 0.1° 量化
    }
}
// 2) 真北 + 校准偏移（放在 VM 层，不在传感器层）
val declination = GeomagneticField(lat.toFloat(), lon.toFloat(), alt.toFloat(), now).declination
val azUI = azSensor + declination + radarCompassOffset        // radarCompassOffset 默认 0f
val pitchUI = pitchSensor + radarCompassOffsetElev
// 3) 等距投影 + 天空图
fun project(azDeg: Float, elDeg: Float, R: Float): Offset {                  // az/el 单位度
    val az = Math.toRadians(azDeg.toDouble()); val el = Math.toRadians(elDeg.toDouble())
    val r = R * (PI / 2 - el) / (PI / 2)                                     // = R*(90-el)/90
    return Offset((r * sin(az)).toFloat(), (-r * cos(az)).toFloat())         // y 向下 ⇒ 0° 朝上
}
Canvas(Modifier.fillMaxSize()) {
    val R = min(size.width, size.height) / 2f * 0.95f
    rotate(-azUI + if (shouldFlipRadar) 180f else 0f, Offset(size.width / 2, size.height / 2)) {
        translate(size.width / 2, size.height / 2) {
            for (i in 0..2) drawCircle(ring, R * (1f - i / 3f), style = Stroke(6f))   // R, 2R/3, R/3
            drawLine(ring, Offset(-R, 0f), Offset(R, 0f), 6f); drawLine(ring, Offset(0f, -R), Offset(0f, R), 6f)
            drawCircle(point, 16f, project(azUI, elUI, R))                            // 卫星点
            drawCircle(ring, 18f, project(azUI, max(0f, -pitchUI), R), style = Stroke(6f))  // 手机指向
        }
    }
}
```

---

## 五、它的坑与不该抄的地方

1. **不判 `getDefaultSensor(11) == null`**。无陀螺仪/旋转向量的设备直接静默失效（UI 恒 0°）。必须判空并降级到"加速度+磁场自算"或隐藏罗盘。
2. **注册不受生命周期约束**。设置开着就 62.5 Hz 常驻，`onPause`/灭屏不注销。应改成 `repeatOnLifecycle(STARTED) { }` 或在 `DisposableEffect` 里成对 register/unregister。
3. **方位角旋转不 unwrap**：`rotate(-azimuth)` 在 0/360 边界整圈闪跳（Q7）。必须自己做最短路径累加。
4. **raW 62.5 Hz 写 StateFlow**，每次回调都发新值 → Compose 每帧重组。它靠 0.1° 量化 + `data class equals` 部分缓解，但仍有大量无效重组。建议 `sample(16.ms)` 或 `conflate()`。
5. **磁偏角靠 `GeomagneticField`（依赖定位）**：无定位权限/定位关闭时 `declination` 用的是最后已知坐标，可能偏差数度。文档里 UI 没有提示"当前是真北还是磁北"。要做的话给用户一个 `磁北/真北` 开关。
6. **`shouldFlipRadar` 的取值诡异**：`j22.java:104-115` 里它由 `radarCompassOffsetElev < 0`（俯仰校准偏移为负）推导出来，语义与"翻转雷达"无关，属于耦合错误/疑似 bug。别抄这个推导。
7. **`p01.s` 环标签只有 90/60/30 三个，且是"天顶角"**，容易被误读成仰角。若要贴仰角刻度，标签应写 `0°/30°/60°`。
8. **没有 N/E/S/W 方位字**。靠整图旋转表达朝向，用户必须自己脑补"上=手机朝向"。若要加方位字，注意它们必须画在 `rotate()` 之内才会跟随（或在外部按 `azimuth` 单独旋转每个字）。
9. **强依赖 Compose 的私有不变量**：`p01.H` 用 `floatToRawIntBits` 打包 `Offset` 成 `long`（Compose 的 `Offset` 内部表示），跨 Compose 版本不稳定。移植时直接用 `Offset(x, y)` 即可。
10. **sample 内自带 osmdroid 风格地图**（`Ls81;`/`Ls91;`/`Li81;`），与罗盘无关，别误抄。
11. 包名以 `com.` 开头（`com.rtbishop.look4sat`），混淆后的类名（`Lqg2;` 等）换版本会全变；本文档的价值在**公式与常量**，不在类名。

---

## 六、反编译产物存放位置

目录：`C:\Users\13766\Documents\deepseek-harness\default-workspace\多普勒\reverse\look4sat-decompiled\`
（只放了实际读过的类，未全量解包；共 39 个 `.java`）

与罗盘/天空图直接相关的：

```
qg2.java    ← 传感器（核心，106 行）
i22.java    ← 天空图 DrawScope lambda（所有绘制调用）
p01.java    ← 投影 H / 圆环 t / 标签 s / 指针 q / 图标 r / Composable h
c22.java    ← Radar 屏 Composable（调用 p01.h）
h22.java    ← 雷达 UI 状态 data class（字段名全在里面）
o22.java    ← Radar ViewModel（注册/注销、天体、状态机）
j22.java    ← 设置/定位/传感器 → 状态映射（磁偏角就在这里相加）
g22.java    ← 注册传感器的协程体（含 16000 µs）
fj2.java    ← SharedPreferences key（radarCompassOffset 等）
lo1.java    ← 设置 data class
io1.java    ← 当前卫星位置数据
t91.java    ← DI 工厂
```

侦查脚本（同目录 `多普勒\reverse\`，均为只读、新文件，未改动任何既有脚本）：

```
l4s_compass_scan.py  → l4s_scan.out    包名/版本/字符串命中/方法名命中
l4s_xref_scan.py     → l4s_xref.out    指令级 xref（SensorManager / GeomagneticField 调用者）
l4s_decompile.py                       按类名反编译：python l4s_decompile.py "Lqg2;" "Lp01;"
l4s_probe.py                            xref | callee | draw 三种模式
l4s_probe2.py .. l4s_probe5.py          DrawScope / Math / Canvas 密度统计
l4s_smali.py                           打印某类/方法的 Dalvik 指令
```

复现反编译：

```bash
cd C:\Users\13766\Documents\deepseek-harness\default-workspace\多普勒\reverse
python l4s_decompile.py "Lqg2;" "Lp01;" "Li22;" "Lh22;" "Lj22;" "Lo22;" "Lg22;" "Lc22;" "Lfj2;"
python l4s_probe.py callee "Lp01;" "H"      # 谁在用投影函数
```
