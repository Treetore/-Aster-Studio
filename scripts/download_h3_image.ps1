param(
    [ValidateSet("q4", "q2", "vae", "int8", "all")]
    [string]$Variant = "all",
    [string]$Proxy = "http://127.0.0.1:7897",
    [string]$ComfyRoot = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $ComfyRoot) {
    $candidate = Join-Path (Split-Path -Parent $ProjectRoot) "mian\ComfyUI_windows_portable\ComfyUI"
    if (Test-Path (Join-Path $candidate "main.py")) { $ComfyRoot = $candidate }
    else { $ComfyRoot = Join-Path $ProjectRoot "ComfyUI" }
}
$Dm = Join-Path $ComfyRoot "models\diffusion_models"
$Vae = Join-Path $ComfyRoot "models\vae"
New-Item -ItemType Directory -Force -Path $Dm, $Vae | Out-Null

$items = @{
    vae = @{
        Url = "https://huggingface.co/Comfy-Org/MiniMax-H3-Image/resolve/main/vae/minimax_h3_image_vae_fp16.safetensors"
        Dest = Join-Path $Vae "minimax_h3_image_vae_fp16.safetensors"
    }
    q4 = @{
        Url = "https://huggingface.co/Infinix-io/MiniMax-H3-Image-GGUF/resolve/main/MiniMax-H3-Image-Pruned-Q4_K_M.gguf"
        Dest = Join-Path $Dm "MiniMax-H3-Image-Pruned-Q4_K_M.gguf"
    }
    q2 = @{
        Url = "https://huggingface.co/Infinix-io/MiniMax-H3-Image-GGUF/resolve/main/MiniMax-H3-Image-Pruned-Q2_K.gguf"
        Dest = Join-Path $Dm "MiniMax-H3-Image-Pruned-Q2_K.gguf"
    }
    int8 = @{
        Url = "https://huggingface.co/Jianqiao1/MiniMax-H3-Image-Lossless-Pruned-ComfyUI/resolve/main/minimax_h3_image_wavecut_style_pruned_reference_int8_convrot.safetensors"
        Dest = Join-Path $Dm "minimax_h3_image_wavecut_style_pruned_reference_int8_convrot.safetensors"
    }
}

$selected = if ($Variant -eq "all") { @("vae", "q4") } else { @($Variant) }
foreach ($key in $selected) {
    $item = $items[$key]
    Write-Host "Downloading $key -> $($item.Dest)"
    & curl.exe -L --fail --retry 10 --retry-delay 3 --continue-at - --proxy $Proxy `
        --output $item.Dest $item.Url
    if ($LASTEXITCODE -ne 0) { throw "download failed: $key (exit $LASTEXITCODE)" }
}
Write-Host "Done. Restart or refresh ComfyUI object_info before generating."
