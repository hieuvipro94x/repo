# Cap nhat SX3 Scanner qua Raspberry Pi

Ung dung Windows doc manifest tai:

```text
http://100.121.199.45:8000/updates/manifest.json
```

Du lieu van luu tai may Windows. Qua trinh cap nhat khong ghi de `database.db`,
`product.db` hoac file `.exe.config`.

## 1. Cau hinh Raspberry Pi mot lan

Chep `raspberry/setup-sx3-update-server.sh` len Raspberry Pi va chay:

```bash
chmod +x setup-sx3-update-server.sh
./setup-sx3-update-server.sh
```

Service `sx3-update-server` phuc vu file tinh bang Python tren cong `8000`.
Kiem tra:

```bash
sudo systemctl status sx3-update-server
curl http://127.0.0.1:8000/updates/manifest.json
```

Lenh `curl` se tra loi `404` cho toi khi tai ban cap nhat dau tien len.

## 2. Tao ban cap nhat tren may Windows

Sau khi build cau hinh `Release`, tai thu muc `deploy` chay:

```powershell
.\publish-update.ps1 -Version "3.11.0" -Notes "Them nut cap nhat tu Raspberry Pi"
```

Hai file can day len Raspberry Pi se nam tai:

```text
deploy\server-files\updates\manifest.json
deploy\server-files\updates\sx3-scanner-3.11.0.zip
```

## 3. Tai ban cap nhat len Raspberry Pi

Tu may Windows co `scp`, chay:

```powershell
scp .\server-files\updates\* <pi-user>@100.121.199.45:/srv/sx3-update-server/updates/
```

Thay `<pi-user>` bang tai khoan dang nhap Raspberry Pi. Khong can khoi dong lai
service vi file moi duoc phuc vu ngay lap tuc.

## 4. Hoat dong tren may tram

Khi mo phan mem, nut `CAP NHAT PHAN MEM` nam tren thanh tren cung. Neu
`manifest.json` co phien ban cao hon phien ban hien tai, nut co cham do va hien
thi phien ban moi. Bam nut de tai goi, xac minh SHA-256, dong ung dung, thay
file chuong trinh va mo lai.
