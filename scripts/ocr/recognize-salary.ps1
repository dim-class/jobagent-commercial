# Windows PowerShell 5.1 / built-in Windows OCR only. No network or image files.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $types = @(
        'Windows.Media.Ocr.OcrEngine', 'Windows.Media.Ocr.OcrResult',
        'Windows.Globalization.Language', 'Windows.Storage.Streams.InMemoryRandomAccessStream',
        'Windows.Storage.Streams.DataWriter', 'Windows.Graphics.Imaging.BitmapDecoder',
        'Windows.Graphics.Imaging.SoftwareBitmap', 'Windows.Graphics.Imaging.BitmapTransform',
        'Windows.Graphics.Imaging.BitmapPixelFormat', 'Windows.Graphics.Imaging.BitmapAlphaMode',
        'Windows.Graphics.Imaging.ExifOrientationMode', 'Windows.Graphics.Imaging.ColorManagementMode'
    )
    foreach ($type in $types) { [Type]::GetType("$type, Windows, ContentType=WindowsRuntime", $true) | Out-Null }
    $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    function Await-OcrOperation($operation, [Type]$resultType) {
        $task = $asTask.MakeGenericMethod($resultType).Invoke($null, @($operation))
        if (-not $task.Wait(8000)) { throw 'ocr_timeout' }
        return $task.Result
    }
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage(
        (New-Object Windows.Globalization.Language('en-US')))
    if ($null -eq $engine) { throw 'language_unavailable' }
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $bytes = [Convert]::FromBase64String($request.image)
    if ($bytes.Length -gt 262144) { throw 'image_too_large' }
    $stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
    $writer = New-Object Windows.Storage.Streams.DataWriter($stream)
    $writer.WriteBytes($bytes)
    Await-OcrOperation ($writer.StoreAsync()) ([uint32]) | Out-Null
    $stream.Seek(0)
    $decoder = Await-OcrOperation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $texts = @()
    foreach ($scale in @(2, 3)) {
        $transform = New-Object Windows.Graphics.Imaging.BitmapTransform
        $transform.ScaledWidth = $decoder.PixelWidth * $scale
        $transform.ScaledHeight = $decoder.PixelHeight * $scale
        if ($transform.ScaledWidth -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension) { throw 'image_too_wide' }
        $bitmap = Await-OcrOperation ($decoder.GetSoftwareBitmapAsync(
            [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
            [Windows.Graphics.Imaging.BitmapAlphaMode]::Ignore, $transform,
            [Windows.Graphics.Imaging.ExifOrientationMode]::IgnoreExifOrientation,
            [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage)) ([Windows.Graphics.Imaging.SoftwareBitmap])
        try {
            $result = Await-OcrOperation ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
            $texts += $result.Text
        } finally { $bitmap.Dispose() }
    }
    @{ texts = $texts; engine = 'windows_local_ocr' } | ConvertTo-Json -Compress
    $writer.DetachStream() | Out-Null
    $stream.Dispose()
} catch {
    # Never echo image bytes, request data, exception objects or private paths.
    [Console]::Out.WriteLine('{"texts":[],"error":"local_ocr_unavailable"}')
    exit 1
}
