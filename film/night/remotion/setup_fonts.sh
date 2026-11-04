#!/bin/bash
# copy OFL fonts from @fontsource into public/fonts
cd "$(dirname "$0")"
mkdir -p public/fonts
cp node_modules/@fontsource/inter/files/inter-latin-{400,500,600}-normal.woff2 node_modules/@fontsource/jetbrains-mono/files/jetbrains-mono-latin-{400,500}-normal.woff2 public/fonts/
