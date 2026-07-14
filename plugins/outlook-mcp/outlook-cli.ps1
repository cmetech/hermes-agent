param(
    [Parameter(Position=0)] [string]$Command,
    [Parameter(Position=1)] [string]$SubCommand,
    [Parameter(Position=2)] [string]$PositionalArg,
    [int]$limit = 10,
    [string]$folder = "Inbox",
    [string]$query,
    [string]$from,
    [string]$subject,
    [string]$to,
    [string]$category,
    [switch]$unread,
    [string]$since,
    [string]$mailbox,
    [string]$body,
    [string]$cc,
    [string]$attachment,
    [switch]$replyAll,
    [switch]$send,
    [string]$path,
    [int]$days = 3,
    [string]$start,
    [string]$end,
    [string]$location
)

$outlook = New-Object -ComObject Outlook.Application
$ns = $outlook.GetNamespace("MAPI")

function Get-FolderByName($store, $name) {
    $map = @{ "Inbox"=6; "SentMail"=5; "Drafts"=16; "DeletedItems"=3 }
    if ($map.ContainsKey($name)) {
        if ($store) { return $store.GetDefaultFolder($map[$name]) }
        return $ns.GetDefaultFolder($map[$name])
    }
    if ($store) { return $store.GetDefaultFolder(6) }
    return $ns.GetDefaultFolder(6)
}

function Get-Store($mailboxName) {
    if (-not $mailboxName) { return $null }
    foreach ($store in $ns.Stores) {
        if ($store.DisplayName -like "*$mailboxName*") { return $store }
    }
    return $null
}

function Format-Table-Row($id, $from, $subj, $date, $att) {
    if ($from.Length -gt 28) { $from = $from.Substring(0,28) + ".." }
    if ($subj.Length -gt 48) { $subj = $subj.Substring(0,48) + ".." }
    $attStr = if ($att -gt 0) { " [$att]" } else { "" }
    "{0,-4} {1,-30} {2,-50} {3}{4}" -f $id, $from, ($subj), $date, $attStr
}

function Parse-Since($s) {
    if (-not $s) { return $null }
    $num = [int]($s -replace '[^0-9]','')
    $unit = ($s -replace '[0-9]','')
    switch ($unit) {
        "m" { return (Get-Date).AddMinutes(-$num) }
        "h" { return (Get-Date).AddHours(-$num) }
        "d" { return (Get-Date).AddDays(-$num) }
        "w" { return (Get-Date).AddDays(-($num*7)) }
    }
    return $null
}

function Count-RealAttachments($msg) {
    $count = 0
    foreach ($att in $msg.Attachments) {
        $cid = $att.PropertyAccessor.GetProperty("http://schemas.microsoft.com/mapi/proptag/0x3712001F")
        if (-not $cid) { $count++ }
    }
    return $count
}

# --- MAILBOX ---
if ($Command -eq "mailbox" -and $SubCommand -eq "list") {
    foreach ($store in $ns.Stores) {
        Write-Output $store.DisplayName
    }
    exit
}

# --- MESSAGE LIST ---
if ($Command -eq "message" -and $SubCommand -eq "list") {
    $store = Get-Store $mailbox
    $fld = Get-FolderByName $store $folder
    $items = $fld.Items
    $items.Sort("[ReceivedTime]", $true)

    $sinceDate = Parse-Since $since
    $results = @()
    $i = 0
    foreach ($msg in $items) {
        if ($results.Count -ge $limit) { break }
        if ($unread -and $msg.UnRead -eq $false) { continue }
        if ($sinceDate -and $msg.ReceivedTime -lt $sinceDate) { break }
        if ($from -and $msg.SenderName -notlike "*$from*") { continue }
        if ($subject -and $msg.Subject -notlike "*$subject*") { continue }
        if ($to) {
            $found = $false
            foreach ($r in $msg.Recipients) { if ($r.Name -like "*$to*" -or $r.Address -like "*$to*") { $found=$true; break } }
            if (-not $found) { continue }
        }
        if ($category -and $msg.Categories -notlike "*$category*") { continue }
        if ($query) {
            $q = $query
            $match = ($msg.Subject -like "*$q*") -or ($msg.SenderName -like "*$q*") -or ($msg.Body -like "*$q*")
            if (-not $match) { continue }
        }
        $i++
        $results += [PSCustomObject]@{
            ID=$i; From=$msg.SenderName; Subject=$msg.Subject
            Date=$msg.ReceivedTime.ToString("yyyy-MM-dd HH:mm")
            Attachments=(Count-RealAttachments $msg)
        }
    }

    Write-Output ("{0,-4} {1,-30} {2,-50} {3}" -f "ID", "From", "Subject", "Date")
    Write-Output ("{0,-4} {1,-30} {2,-50} {3}" -f "--", "----", "-------", "----")
    foreach ($r in $results) {
        Write-Output (Format-Table-Row $r.ID $r.From $r.Subject $r.Date $r.Attachments)
    }
    exit
}

# --- MESSAGE READ ---
if ($Command -eq "message" -and $SubCommand -eq "read") {
    $id = [int]$PositionalArg
    $store = Get-Store $mailbox
    $fld = Get-FolderByName $store $folder
    $items = $fld.Items
    $items.Sort("[ReceivedTime]", $true)
    $msg = $items.Item($id)

    Write-Output "From:    $($msg.SenderName) <$($msg.SenderEmailAddress)>"
    Write-Output "To:      $(($msg.Recipients | ForEach-Object { $_.Name }) -join '; ')"
    Write-Output "Date:    $($msg.ReceivedTime.ToString('yyyy-MM-dd HH:mm'))"
    Write-Output "Subject: $($msg.Subject)"
    Write-Output ""
    Write-Output $msg.Body
    exit
}

# --- MESSAGE SEND ---
if ($Command -eq "message" -and $SubCommand -eq "send") {
    $mail = $outlook.CreateItem(0)
    $mail.To = $to
    $mail.Subject = $subject
    $mail.Body = $body
    if ($cc) { $mail.CC = $cc }
    if ($attachment) {
        foreach ($f in $attachment -split ";") {
            $f = $f.Trim()
            if ($f -match "^/mnt/([a-z])/(.*)") {
                $f = "$($Matches[1].ToUpper()):\$($Matches[2] -replace '/','\')"
            }
            $mail.Attachments.Add($f) | Out-Null
        }
    }
    if ($send) {
        $mail.Send()
        Write-Output "Email sent to $to."
    } else {
        $mail.Display()
        Write-Output "Draft opened in Outlook."
    }
    exit
}

# --- MESSAGE REPLY ---
if ($Command -eq "message" -and $SubCommand -eq "reply") {
    $id = [int]$PositionalArg
    $store = Get-Store $mailbox
    $fld = Get-FolderByName $store $folder
    $items = $fld.Items
    $items.Sort("[ReceivedTime]", $true)
    $msg = $items.Item($id)

    if ($replyAll) { $reply = $msg.ReplyAll() } else { $reply = $msg.Reply() }
    $reply.Body = $body + "`n`n" + $reply.Body
    $reply.Display()
    Write-Output "Reply draft opened in Outlook."
    exit
}

# --- MESSAGE DELETE ---
if ($Command -eq "message" -and $SubCommand -eq "delete") {
    $id = [int]$PositionalArg
    $store = Get-Store $mailbox
    $fld = Get-FolderByName $store $folder
    $items = $fld.Items
    $items.Sort("[ReceivedTime]", $true)
    $msg = $items.Item($id)
    $msg.Delete()
    Write-Output "Deleted message $id."
    exit
}

# --- MESSAGE ATTACHMENTS DOWNLOAD ---
if ($Command -eq "message" -and $SubCommand -eq "attachments") {
    # outlook-cli message attachments download <ID> --path <dir>
    $action = $PositionalArg
    if ($action -ne "download") { Write-Output "Usage: message attachments download <ID> --path <dir>"; exit 1 }
    # Re-parse: ID is next positional after "download" — grab from remaining args
    $id = [int]$args[0]
    $dlPath = if ($path) { $path } else { "$env:USERPROFILE\Downloads" }
    if ($dlPath -match "^/mnt/([a-z])/(.*)") {
        $dlPath = "$($Matches[1].ToUpper()):\$($Matches[2] -replace '/','\')"
    }

    $store = Get-Store $mailbox
    $fld = Get-FolderByName $store $folder
    $items = $fld.Items
    $items.Sort("[ReceivedTime]", $true)
    $msg = $items.Item($id)

    $count = 0
    foreach ($att in $msg.Attachments) {
        $cid = $att.PropertyAccessor.GetProperty("http://schemas.microsoft.com/mapi/proptag/0x3712001F")
        if (-not $cid) {
            $att.SaveAsFile("$dlPath\$($att.FileName)")
            Write-Output "Saved: $($att.FileName)"
            $count++
        }
    }
    if ($count -eq 0) { Write-Output "No attachments found." }
    exit
}

# --- CALENDAR LIST ---
if ($Command -eq "calendar" -and $SubCommand -eq "list") {
    $cal = $ns.GetDefaultFolder(9) # 9 = olFolderCalendar
    $startDate = (Get-Date).Date
    $endDate = $startDate.AddDays($days)

    $filter = "[Start] >= '$($startDate.ToString('g'))' AND [Start] <= '$($endDate.ToString('g'))'"
    $items = $cal.Items
    $items.IncludeRecurrences = $true
    $items.Sort("[Start]")
    $restricted = $items.Restrict($filter)

    Write-Output ("{0,-4} {1,-40} {2,-20} {3,-20} {4}" -f "ID","Subject","Start","End","Status")
    Write-Output ("{0,-4} {1,-40} {2,-20} {3,-20} {4}" -f "--","-------","-----","---","------")
    $i = 0
    foreach ($evt in $restricted) {
        $i++
        $subj = $evt.Subject
        if ($subject -and $subj -notlike "*$subject*") { continue }
        if ($subj.Length -gt 38) { $subj = $subj.Substring(0,38) + ".." }
        $status = switch ($evt.ResponseStatus) {
            0 {"None"} 1 {"Organizer"} 2 {"Tentative"} 3 {"Accepted"} 4 {"Declined"} 5 {"Not Responded"} default {"Unknown"}
        }
        Write-Output ("{0,-4} {1,-40} {2,-20} {3,-20} {4}" -f $i, $subj, $evt.Start.ToString("yyyy-MM-dd HH:mm"), $evt.End.ToString("yyyy-MM-dd HH:mm"), $status)
    }
    exit
}

# --- CALENDAR CREATE ---
if ($Command -eq "calendar" -and $SubCommand -eq "create") {
    $appt = $outlook.CreateItem(1)
    $appt.Subject = $subject
    $appt.Start = [datetime]$start
    if ($end) { $appt.End = [datetime]$end } else { $appt.End = ([datetime]$start).AddHours(1) }
    if ($body) { $appt.Body = $body }
    if ($location) { $appt.Location = $location }
    if ($to) {
        foreach ($addr in $to -split ";") {
            $appt.Recipients.Add($addr.Trim()) | Out-Null
        }
        $appt.MeetingStatus = 1
    }
    $appt.Save()
    Write-Output "Event created: $subject"
    exit
}

# --- CALENDAR UPDATE ---
if ($Command -eq "calendar" -and $SubCommand -eq "update") {
    $id = [int]$PositionalArg
    $cal = $ns.GetDefaultFolder(9)
    $startDate = (Get-Date).Date
    $endDate = $startDate.AddDays(30)
    $filter = "[Start] >= '$($startDate.ToString('g'))' AND [Start] <= '$($endDate.ToString('g'))'"
    $items = $cal.Items
    $items.IncludeRecurrences = $true
    $items.Sort("[Start]")
    $restricted = $items.Restrict($filter)
    $i = 0
    foreach ($evt in $restricted) {
        $i++
        if ($i -eq $id) {
            if ($subject) { $evt.Subject = $subject }
            if ($start) { $evt.Start = [datetime]$start }
            if ($end) { $evt.End = [datetime]$end }
            if ($body) { $evt.Body = $body }
            if ($location) { $evt.Location = $location }
            $evt.Save()
            Write-Output "Updated event $id."
            exit
        }
    }
    Write-Output "Event $id not found."
    exit
}

# --- CALENDAR DELETE ---
if ($Command -eq "calendar" -and $SubCommand -eq "delete") {
    $id = [int]$PositionalArg
    $cal = $ns.GetDefaultFolder(9)
    $startDate = (Get-Date).Date
    $endDate = $startDate.AddDays(30)
    $filter = "[Start] >= '$($startDate.ToString('g'))' AND [Start] <= '$($endDate.ToString('g'))'"
    $items = $cal.Items
    $items.IncludeRecurrences = $true
    $items.Sort("[Start]")
    $restricted = $items.Restrict($filter)
    $i = 0
    foreach ($evt in $restricted) {
        $i++
        if ($i -eq $id) {
            $evt.Delete()
            Write-Output "Deleted event $id."
            exit
        }
    }
    Write-Output "Event $id not found."
    exit
}

# --- CALENDAR ACCEPT ---
if ($Command -eq "calendar" -and $SubCommand -eq "accept") {
    $id = [int]$PositionalArg
    $cal = $ns.GetDefaultFolder(9)
    $startDate = (Get-Date).Date
    $endDate = $startDate.AddDays(30)
    $filter = "[Start] >= '$($startDate.ToString('g'))' AND [Start] <= '$($endDate.ToString('g'))'"
    $items = $cal.Items
    $items.IncludeRecurrences = $true
    $items.Sort("[Start]")
    $restricted = $items.Restrict($filter)
    $i = 0
    foreach ($evt in $restricted) {
        $i++
        if ($i -eq $id) {
            $evt.Respond(3) | Out-Null  # 3 = olMeetingAccepted
            Write-Output "Accepted event $id."
            exit
        }
    }
    Write-Output "Event $id not found."
    exit
}

Write-Output "Usage: outlook-cli <command> <subcommand> [options]"
Write-Output "Commands: mailbox list | message list/read/send/reply/delete/attachments | calendar list/create/update/delete/accept"
