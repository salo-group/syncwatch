import java.awt.Dimension;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import javax.swing.JFrame;
import javax.swing.SwingUtilities;
import javax.swing.Timer;
import org.jetbrains.skia.PixelGeometry;
import org.jetbrains.skiko.SkiaLayer;
import org.jetbrains.skiko.SkiaLayerAnalytics;
import org.jetbrains.skiko.SkiaLayerProperties;

final class LinuxDisplayProbe {
    private final AtomicInteger frames = new AtomicInteger();
    private final AtomicReference<Dimension> renderedSize = new AtomicReference<>();
    private final JFrame window = new JFrame("SyncWatch private display check");
    private final SkiaLayer layer = new SkiaLayer(null, new SkiaLayerProperties(),
        SkiaLayerAnalytics.Companion.getEmpty(), PixelGeometry.UNKNOWN);
    private final Timer redraw = new Timer(50, event -> layer.needRedraw());

    private LinuxDisplayProbe() {
        layer.setRenderDelegate((canvas, width, height, time) -> {
            if (width > 0 && height > 0) renderedSize.set(new Dimension(width, height));
            int number = frames.incrementAndGet();
            canvas.clear(number % 2 == 0 ? 0xff337799 : 0xff994466);
        });
        window.add(layer);
        window.setSize(480, 320);
        window.setVisible(true);
        redraw.start();
    }

    private void checkFrames(String phase) throws InterruptedException {
        Thread.sleep(1250);
        int before = frames.get();
        Thread.sleep(1100);
        int received = frames.get() - before;
        System.out.printf("%s: %d redraws%n", phase, received);
        if (received < 12) throw new IllegalStateException("Redraw stalled at " + phase);
    }

    private static String xrandr(String... arguments) throws Exception {
        String[] command = new String[arguments.length + 1];
        command[0] = "xrandr";
        System.arraycopy(arguments, 0, command, 1, arguments.length);
        Process process = new ProcessBuilder(command).redirectErrorStream(true).start();
        if (!process.waitFor(4, TimeUnit.SECONDS)) {
            process.destroyForcibly().waitFor();
            throw new IllegalStateException("XRandR did not return");
        }
        String output = new String(process.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
        if (process.exitValue() != 0) throw new IllegalStateException(output);
        return output;
    }

    private static void mode(String name, String clock) throws Exception {
        xrandr("--output", "screen", "--mode", name);
        boolean active = xrandr("--current", "--verbose").lines().map(String::strip)
            .anyMatch(line -> line.startsWith(name + " ") && line.contains(clock) && line.contains("*current"));
        if (!active) throw new IllegalStateException("Unexpected private display mode");
    }

    public static void main(String[] arguments) throws Exception {
        for (String name : new String[]{"sw-60", "sw-zero"}) {
            xrandr("--newmode", name, name.equals("sw-60") ? "52.224" : "0",
                "1024", "1040", "1056", "1088", "768", "770", "776", "800");
            xrandr("--addmode", "screen", name);
        }
        mode("sw-60", "52.224MHz");
        LinuxDisplayProbe[] probe = new LinuxDisplayProbe[1];
        SwingUtilities.invokeAndWait(() -> probe[0] = new LinuxDisplayProbe());
        try {
            probe[0].checkFrames("60 Hz");
            Dimension originalSize = probe[0].renderedSize.get();
            mode("sw-zero", "0.000MHz");
            probe[0].checkFrames("0 Hz");
            mode("sw-60", "52.224MHz");
            SwingUtilities.invokeAndWait(() -> probe[0].window.setSize(560, 380));
            probe[0].checkFrames("recovered and resized");
            Dimension recoveredSize = probe[0].renderedSize.get();
            if (originalSize == null || recoveredSize == null || recoveredSize.width <= originalSize.width
                || recoveredSize.height <= originalSize.height) {
                throw new IllegalStateException("Recovered renderer did not draw at the new size");
            }
        } finally {
            SwingUtilities.invokeAndWait(() -> {
                probe[0].redraw.stop();
                probe[0].layer.detach();
                probe[0].window.dispose();
            });
        }
    }
}
