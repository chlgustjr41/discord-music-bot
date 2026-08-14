namespace Loupedeck.JackyControlPlugin;

using System;
using System.Collections.Generic;

/// <summary>
/// Two-line key title layout (no marquee): greedy word wrap, hard-break for a
/// word longer than the line, and when content remains past the last line the
/// last line is cut to maxCharsPerLine - 1 chars plus an ellipsis.
/// </summary>
public static class KeyText
{
    public static String[] TitleLines(String title, Int32 maxCharsPerLine, Int32 maxLines)
    {
        if (String.IsNullOrWhiteSpace(title) || maxCharsPerLine < 1 || maxLines < 1)
        {
            return Array.Empty<String>();
        }
        var text = String.Join(" ", title.Split((Char[])null, StringSplitOptions.RemoveEmptyEntries));
        var lines = new List<String>();
        var starts = new List<Int32>();
        var pos = 0;
        while (pos < text.Length)
        {
            starts.Add(pos);
            Int32 end;
            if (pos + maxCharsPerLine >= text.Length)
            {
                end = text.Length; // the rest fits
            }
            else if (text[pos + maxCharsPerLine] == ' ')
            {
                end = pos + maxCharsPerLine; // full window lands on a word boundary
            }
            else
            {
                var space = text.LastIndexOf(' ', pos + maxCharsPerLine - 1);
                end = space > pos
                    ? space // break at the last word boundary that fits
                    : pos + maxCharsPerLine; // single overlong word: hard break
            }
            lines.Add(text[pos..end]);
            pos = end;
            while (pos < text.Length && text[pos] == ' ')
            {
                pos++;
            }
        }
        if (lines.Count <= maxLines)
        {
            return lines.ToArray();
        }
        // Content remains past maxLines: rebuild the last line from the flat
        // remaining text (not the wrapped line, which may be shorter because
        // of the word boundary) and ellipsize.
        var kept = lines.GetRange(0, maxLines);
        var lastStart = starts[maxLines - 1];
        var cut = Math.Min(maxCharsPerLine - 1, text.Length - lastStart);
        kept[maxLines - 1] = text.Substring(lastStart, cut) + "…";
        return kept.ToArray();
    }
}
