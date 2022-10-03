#!/bin/bash -
# https://wiki.archlinux.org/title/Python_package_guidelines

(
cat << 'EOF'
# Maintainer: Benawi Adha <benawiadha@gmail.com>
# Contributor: Spencer Muise <smuise@spencermuise.ca>

pkgname=epy-git
_name=epy
provides=('epy')
pkgver=EPY_PKGVER
pkgrel=1
pkgdesc="TUI Ebook Reader"
arch=('any')
url='https://github.com/wustho/epy'
license=("GPL3")
conflicts=("epy")
depends=(
  'python'
)
makedepends=(
  'git'
  'python-build'
  'python-installer'
  'python-wheel'
)
source=("git+https://github.com/wustho/$_name.git")
sha256sums=('SKIP')

pkgver() {
  cd "$_name"
  printf "%s.r%s.%s" "$(grep -F '__version__ =' src/epy_reader/__init__.py | awk -F\" '{print $2}')" "$(git rev-list --count HEAD)" "$(git rev-parse --short HEAD)"
}

build() {
    cd "$_name"
    python -m build --wheel --no-isolation
}

package() {
    install -D "$srcdir/$_name/LICENSE" "$pkgdir/usr/share/licenses/$pkgname"
    cd "$_name"
    python -m installer --destdir="$pkgdir" dist/*.whl
}
EOF
) | sed 's/pkgver=EPY_PKGVER/pkgver='`printf "%s.r%s.%s" "$(grep -F '__version__ =' src/epy_reader/__init__.py | awk -F\" '{print $2}')" "$(git rev-list --count HEAD)" "$(git rev-parse --short HEAD)"`'/'
