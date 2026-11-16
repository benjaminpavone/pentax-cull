package local.pentaxcull;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.IOException;
import java.io.OutputStream;

/** Where the photos come from: the camera (Wi-Fi) or a folder / memory card. Same contract as the Mac sources. */
interface Source {
    interface Progress { boolean update(long done, long size); }

    String type();                       // "camera" or "folder"
    String label();
    boolean writable();                  // xmp:Rating can be written into the files
    boolean removable();                 // memory card / USB drive: no rating written into the files by default

    JSONArray list() throws Exception;
    JSONObject info(String id) throws Exception;
    /** size: "screen" (fits the screen) or "full" (full-resolution embedded JPEG). */
    byte[] preview(String id, String size) throws Exception;
    /** Streams the original file into `out`. Returns false if progress() asked to stop. */
    boolean fetch(String id, OutputStream out, Progress progress) throws IOException;
    String writeRating(String id, int rating) throws Exception;
    void close();
}
