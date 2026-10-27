import com.f4hwn.doppler.orbit.Sgp4
import com.f4hwn.doppler.orbit.Tle
import java.io.File
import kotlin.math.sqrt

/**
 * 拿 python-sgp4 的真值核对 Kotlin 的 SGP4/SDP4。
 * 这不是 Android 的一部分，只是给 gradle/CLI 编译后跑的自检程序。
 */
fun main(args: Array<String>) {
    val path = args.getOrNull(0) ?: "_sdp4_truth.tsv"
    val lines = File(path).readLines().filter { it.isNotBlank() }

    data class Case(val norad: String, val name: String, val tle: Tle)
    val cases = LinkedHashMap<String, Case>()
    val rows = mutableListOf<Triple<String, Double, DoubleArray>>() // norad, t, [x,y,z,err]

    for (ln in lines) {
        val f = ln.split('\t')
        when (f[0]) {
            "C" -> cases[f[1]] = Case(f[1], f[2], Tle(f[2], f[3], f[4]))
            "R" -> rows += Triple(f[1], f[2].toDouble(), doubleArrayOf(f[3].toDouble(), f[4].toDouble(), f[5].toDouble(), f[6].toDouble()))
        }
    }

    var worstAll = 0.0
    var fail = 0
    val verboseNorad = args.getOrNull(1)
    for ((norad, c) in cases) {
        val sgp = Sgp4(c.tle)
        var worst = 0.0
        var worstT = 0.0
        var n = 0
        for ((rNorad, t, expect) in rows) {
            if (rNorad != norad) continue
            if (expect[3] != 0.0) continue // python 报了错，跳过
            val r = sgp.propagate(t)
            val dx = r.x - expect[0]
            val dy = r.y - expect[1]
            val dz = r.z - expect[2]
            val d = sqrt(dx * dx + dy * dy + dz * dz)
            n++
            if (norad == verboseNorad) println("    t=%7.0f  d=%10.7f km".format(t, d))
            if (d > worst) { worst = d; worstT = t }
        }
        val ok = worst < 1.0
        if (!ok) fail++
        if (worst > worstAll) worstAll = worst
        println("%-6s %-8s deep=%-5s n=%3d   max_dev %10.7f km @ t=%.0f min  %s"
            .format(norad, c.name, sgp.isDeepSpace, n, worst, worstT, if (ok) "OK" else "FAIL"))
    }
    println("---")
    println("总 max_dev %.7f km，fail %d".format(worstAll, fail))
    if (fail > 0) throw IllegalStateException("SGP4 与参考实现不一致")
}
