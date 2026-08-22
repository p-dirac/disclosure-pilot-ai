[CmdletBinding()]
param (
    [Parameter(Mandatory = $false, Position = 0)]
	[string]$Path = ".",

    [Parameter(Mandatory = $false)]
    [string[]]$Exclude = @('node_modules', '__pycache__', '.venv', '.pytest_cache', '.git', '.vs', 'bin', 'obj'),

    [Parameter(Mandatory = $false)]
    [int]$MaxDepth = 3,

    [Parameter(Mandatory = $false)]
    [string]$OutputPath
)

# Safe box-drawing characters using Unicode hex codes
$bLast = "$([char]0x2514)$([char]0x2500)$([char]0x2500) " # └── 
$bMid  = "$([char]0x251C)$([char]0x2500)$([char]0x2500) " # ├── 
$bPipe = "$([char]0x2502)   "                            # │   

function Build-TreeLines {
    param (
        [string] $CurrentPath,
        [string[]] $ExcludeList,
        [int] $MaxDepth,
        [string] $Indent = ""
    )

    $results = [System.Collections.Generic.List[string]]::new()
    $itemPath = Get-Item -Path $CurrentPath -ErrorAction Stop

    if ($Indent -eq "") {
        $results.Add("$($itemPath.Name)/")
    }

    if (($Indent.Length / 4) -ge $MaxDepth) {
        return $results
    }

	# Added -Directory flag to filter out files at the source
    $items = Get-ChildItem -Path $itemPath.FullName -Directory -ErrorAction SilentlyContinue | Where-Object {
        $itemName = $_.Name
        $isExcluded = $false
        foreach ($pattern in $ExcludeList) {
            if ($itemName -like $pattern) {
                $isExcluded = $true
                break
            }
        }
        -not $isExcluded
    }

    $count =$items.Count
    $i = 0

    foreach ($item in $items) {
        $i++
		$isLast = ($i -eq $count)

        $branch     = if ($isLast) {$bLast } else { $bMid } $nextIndent = if ($isLast) { "$Indent    " } else { "$Indent$bPipe" }

        $results.Add("$Indent$branch$($item.Name)/")
        [string[]] $subResults = Build-TreeLines -CurrentPath $item.FullName -ExcludeList $ExcludeList -MaxDepth $MaxDepth -Indent $nextIndent
        if ($null -ne $subResults) {
			$results.AddRange($subResults)
		}
    }

    return $results
}

$treeOutput = Build-TreeLines -CurrentPath $Path -ExcludeList $Exclude -MaxDepth $MaxDepth

foreach ($line in $treeOutput) {
    if ($line -match '/$') {
        Write-Host $line -ForegroundColor Yellow
    } else {
        Write-Host $line -ForegroundColor White
    }
}

if (![string]::IsNullOrWhiteSpace($OutputPath)) {
    $fullOutputPath = [System.IO.Path]::GetFullPath($OutputPath)
    $NL = [Environment]::NewLine 
	$joinedTree = $treeOutput -join $NL

    if ($OutputPath.EndsWith(".md", [System.StringComparison]::OrdinalIgnoreCase)) {
        $codeFence = '```'
        $mdLines = @(
            "# Directory Structure",
            "",
            "Root: $Path",
            "",
            $codeFence,
            $joinedTree,
            $codeFence
        )
        $mdContent = $mdLines -join $NL
        Set-Content -Path $fullOutputPath -Value $mdContent -Encoding UTF8
    } else {
        Set-Content -Path $fullOutputPath -Value $joinedTree -Encoding UTF8
    }

    Write-Host "`n[+] Directory tree successfully saved to: $fullOutputPath" -ForegroundColor Green
}