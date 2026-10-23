# Build the Chinese architecture report using installed Microsoft Word on Windows.
# Run from repository root. Source figure paths are repository relative.
# Noto Sans SC must be installed; PDF is an internal visual-QA output.
# Native COM adaptation used because the hosted artifact renderer is unavailable.
param(
  [Parameter(Mandatory=$true)][string]$ContentPath,
  [Parameter(Mandatory=$true)][string]$DocxPath,
  [Parameter(Mandatory=$true)][string]$PdfPath
)
$ErrorActionPreference = 'Stop'
$reportInput = Get-Content -LiteralPath $ContentPath -Raw -Encoding UTF8 | ConvertFrom-Json
$reportWord = $null
$reportDocument = $null
try {
  $reportWord = New-Object -ComObject Word.Application
  $reportWord.Visible = $false
  $reportWord.DisplayAlerts = 0
  $reportDocument = $reportWord.Documents.Add()
  $reportDocument.PageSetup.PageWidth = 612
  $reportDocument.PageSetup.PageHeight = 792
  $reportDocument.PageSetup.TopMargin = 54
  $reportDocument.PageSetup.BottomMargin = 54
  $reportDocument.PageSetup.LeftMargin = 54
  $reportDocument.PageSetup.RightMargin = 54
  $reportDocument.PageSetup.LayoutMode = 0
  foreach ($styleId in @(-1,-2,-3,-4,-63,-75)) {
    $style = $reportDocument.Styles.Item($styleId)
    $style.Font.Name = 'Noto Sans SC'
    $style.Font.NameFarEast = 'Noto Sans SC'
    $style.Font.Color = 0
    $style.Font.Underline = 0
    $style.ParagraphFormat.Borders.Enable = 0
  }
  $body = $reportDocument.Styles.Item(-1)
  $body.Font.Size = 11
  $body.ParagraphFormat.SpaceAfter = 7
  $body.ParagraphFormat.LineSpacingRule = 4
  $body.ParagraphFormat.LineSpacing = 17
  $body.ParagraphFormat.DisableLineHeightGrid = -1
  $body.ParagraphFormat.WidowControl = -1
  foreach ($heading in @(@(-2,16),@(-3,13),@(-4,11.5))) {
    $style = $reportDocument.Styles.Item([int]$heading[0])
    $style.Font.Size = [single]$heading[1]
    $style.Font.Bold = -1
    $style.ParagraphFormat.KeepWithNext = -1
    $style.ParagraphFormat.SpaceBefore = 14
    $style.ParagraphFormat.SpaceAfter = 7
  }
  $reportDocument.Styles.Item(-63).Font.Size = 24
  $reportDocument.Styles.Item(-63).ParagraphFormat.SpaceAfter = 12
  $reportDocument.Styles.Item(-63).ParagraphFormat.LineSpacingRule = 3
  $reportDocument.Styles.Item(-63).ParagraphFormat.LineSpacing = 32
  $reportDocument.Styles.Item(-63).ParagraphFormat.KeepWithNext = -1
  $reportDocument.Styles.Item(-75).Font.Size = 11
  $reportDocument.Styles.Item(-75).ParagraphFormat.LineSpacingRule = 3
  $reportDocument.Styles.Item(-75).ParagraphFormat.LineSpacing = 17
  function Add-ReportParagraph([string]$Text,[int]$StyleId=-1) {
    $start = $reportDocument.Content.End - 1
    $range = $reportDocument.Range($start,$start)
    $range.InsertAfter($Text + "`r")
    $paragraph = $reportDocument.Range($start,$reportDocument.Content.End - 1)
    $paragraph.Style = $reportDocument.Styles.Item($StyleId)
    $paragraph.Font.Color = 0
    $paragraph.Font.Underline = 0
    $paragraph.ParagraphFormat.Borders.Enable = 0
  }
  Add-ReportParagraph ([string]$reportInput.title) -63
  Add-ReportParagraph ([string]$reportInput.subtitle) -75
  foreach ($block in $reportInput.blocks) {
    switch ($block.type) {
      'heading' { Add-ReportParagraph ([string]$block.text) (-1 - [int]$block.level) }
      'paragraph' { Add-ReportParagraph ([string]$block.text) }
      'pagebreak' {
        $position = $reportDocument.Content.End - 1
        $reportDocument.Range($position,$position).InsertBreak(7)
      }
      'figure' {
        $position = $reportDocument.Content.End - 1
        $figureRange = $reportDocument.Range($position,$position)
        $figure = $reportDocument.InlineShapes.AddPicture([IO.Path]::GetFullPath([string]$block.path),$false,$true,$figureRange)
        $figure.LockAspectRatio = -1
        $figure.Width = 504
        $figure.Range.ParagraphFormat.LineSpacingRule = 0
        $figure.Range.ParagraphFormat.Alignment = 1
        $figure.Range.ParagraphFormat.SpaceAfter = 7
        $afterFigure = $reportDocument.Range($figure.Range.End,$figure.Range.End)
        $afterFigure.InsertAfter("`r")
        if ($block.caption) { Add-ReportParagraph ([string]$block.caption) }
      }
      'table' {
        if ($block.caption) { Add-ReportParagraph ([string]$block.caption) }
        $position = $reportDocument.Content.End - 1
        $table = $reportDocument.Tables.Add($reportDocument.Range($position,$position),$block.rows.Count + 1,$block.headers.Count)
        $table.AllowAutoFit = $false
        $table.PreferredWidthType = 3
        $table.PreferredWidth = 504
        $table.TopPadding = 5
        $table.BottomPadding = 5
        $table.LeftPadding = 7
        $table.RightPadding = 7
        $table.Range.Font.Name = 'Noto Sans SC'
        $table.Range.Font.NameFarEast = 'Noto Sans SC'
        $table.Range.Font.Size = 10.5
        $table.Range.Font.Color = 0
        $table.Range.ParagraphFormat.SpaceBefore = 0
        $table.Range.ParagraphFormat.SpaceAfter = 2
        $table.Range.ParagraphFormat.KeepWithNext = 0
        $table.Range.ParagraphFormat.Alignment = 0
        $table.Range.ParagraphFormat.DisableLineHeightGrid = -1
        $table.Range.ParagraphFormat.LineSpacingRule = 4
        $table.Range.ParagraphFormat.LineSpacing = 16
        $table.Range.Cells.VerticalAlignment = 1
        $table.Rows.AllowBreakAcrossPages = 0
        foreach ($borderId in @(1,2,3,4,5,6)) {
          $table.Borders.Item($borderId).LineStyle = 1
          $table.Borders.Item($borderId).Color = 14277081
          $table.Borders.Item($borderId).LineWidth = 4
        }
        for ($column=1; $column -le $block.headers.Count; $column++) {
          $table.Columns.Item($column).Width = [single]$block.widths[$column-1]
          $table.Cell(1,$column).Range.Text = [string]$block.headers[$column-1]
          for ($row=1; $row -le $block.rows.Count; $row++) {
            $table.Cell($row+1,$column).Range.Text = [string]$block.rows[$row-1][$column-1]
          }
          for ($row=1; $row -le ($block.rows.Count+1); $row++) {
            $table.Cell($row,$column).Range.ParagraphFormat.Alignment = $(if ($block.centerColumns -contains ($column-1)) { 1 } else { 0 })
          }
        }
        $table.Rows.Item(1).HeadingFormat = -1
        $table.Rows.Item(1).Range.Font.Bold = -1
        $table.Rows.Item(1).Shading.BackgroundPatternColor = 16183528
        $afterTable = $reportDocument.Range($table.Range.End,$table.Range.End)
        $afterTable.InsertAfter("`r")
      }
      default { throw "Unsupported report block type: $($block.type)" }
    }
  }
  $footer = $reportDocument.Sections.Item(1).Footers.Item(1).Range
  $footer.Font.Name = 'Noto Sans SC'
  $footer.Font.NameFarEast = 'Noto Sans SC'
  $footer.Font.Size = 9
  $footer.Font.Color = 0
  $footer.ParagraphFormat.Alignment = 2
  [void]$footer.Fields.Add($footer,-1,'PAGE')
  $reportDocument.Sections.Item(1).Footers.Item(1).Range.ParagraphFormat.Alignment = 2
  $reportDocument.Fields.Update() | Out-Null
  $reportDocument.Repaginate()
  [string]$outputDocx = [IO.Path]::GetFullPath($DocxPath)
  [string]$outputPdf = [IO.Path]::GetFullPath($PdfPath)
  $reportDocument.SaveAs2($outputDocx,16)
  $reportDocument.ExportAsFixedFormat($outputPdf,17)
  [PSCustomObject]@{docx=$outputDocx; pdf=$outputPdf; pages=$reportDocument.ComputeStatistics(2)} | ConvertTo-Json -Compress
} finally {
  if ($reportDocument) { $reportDocument.Close(0); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($reportDocument) }
  if ($reportWord) { $reportWord.Quit(); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($reportWord) }
}
# Use document-specific metadata, not the host Office account identity.
Add-Type -AssemblyName System.IO.Compression.FileSystem
Add-Type -AssemblyName System.IO.Compression
$reportArchive = [IO.Compression.ZipFile]::Open($outputDocx,[IO.Compression.ZipArchiveMode]::Update)
try {
  $entry = $reportArchive.GetEntry('docProps/core.xml')
  $reader = New-Object IO.StreamReader($entry.Open())
  try { [xml]$coreXml = $reader.ReadToEnd() } finally { $reader.Dispose() }
  $ns = New-Object Xml.XmlNamespaceManager($coreXml.NameTable)
  $ns.AddNamespace('dc','http://purl.org/dc/elements/1.1/')
  $ns.AddNamespace('cp','http://schemas.openxmlformats.org/package/2006/metadata/core-properties')
  foreach ($field in @(@('dc:title','dc','title','http://purl.org/dc/elements/1.1/',[string]$reportInput.title),@('dc:creator','dc','creator','http://purl.org/dc/elements/1.1/','Ontology-Farm'),@('cp:lastModifiedBy','cp','lastModifiedBy','http://schemas.openxmlformats.org/package/2006/metadata/core-properties','Ontology-Farm'))) {
    $node = $coreXml.SelectSingleNode('/cp:coreProperties/'+$field[0],$ns)
    if (-not $node) { $node=$coreXml.CreateElement($field[1],$field[2],$field[3]); [void]$coreXml.DocumentElement.AppendChild($node) }
    $node.InnerText = $field[4]
  }
  $entry.Delete()
  $replacement = $reportArchive.CreateEntry('docProps/core.xml')
  $writer = New-Object IO.StreamWriter($replacement.Open(),(New-Object Text.UTF8Encoding($false)))
  try { $coreXml.Save($writer) } finally { $writer.Dispose() }
} finally { $reportArchive.Dispose() }
